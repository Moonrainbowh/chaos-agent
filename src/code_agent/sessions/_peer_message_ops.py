from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from code_agent.peers.errors import (
    PeerAmbiguousError,
    PeerIdentityError,
    PeerNotFoundError,
    PeerQueueFullError,
    PeerRateLimitError,
)
from code_agent.peers.models import (
    PeerInboundPolicy,
    PeerMessageStatus,
    PeerSessionStatus,
    validate_peer_content,
)

from ._codec import encode_datetime
from ._peer_rows import peer_session_from_row
from ._records import _text


def validate_message_text(content: object) -> str:
    return validate_peer_content(_text(content, "content"))


def validate_limit(value: object, name: str, maximum: int = 100_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def resolve_recipient(
    connection: sqlite3.Connection,
    sender_instance_id: str,
    target: str,
    stale_before: datetime,
) -> sqlite3.Row:
    target = _text(target, "target")
    cutoff = encode_datetime(stale_before)
    row = connection.execute(
        "SELECT * FROM peer_sessions WHERE session_ref = ? COLLATE NOCASE "
        "AND instance_id <> ? AND status <> 'closed' "
        "AND julianday(heartbeat_at) >= julianday(?)",
        (target, sender_instance_id, cutoff),
    ).fetchone()
    if row is not None:
        return row
    rows = connection.execute(
        "SELECT * FROM peer_sessions WHERE name = ? COLLATE NOCASE "
        "AND instance_id <> ? AND status <> 'closed' "
        "AND julianday(heartbeat_at) >= julianday(?) "
        "ORDER BY session_ref COLLATE NOCASE",
        (target, sender_instance_id, cutoff),
    ).fetchall()
    if not rows:
        raise PeerNotFoundError("live peer target not found")
    if len(rows) > 1:
        raise PeerAmbiguousError(target, tuple(row["session_ref"] for row in rows))
    return rows[0]


def initial_message_status(
    sender_row: sqlite3.Row, receiver_row: sqlite3.Row
) -> PeerMessageStatus:
    sender = peer_session_from_row(sender_row)
    receiver = peer_session_from_row(receiver_row)
    if sender.status is PeerSessionStatus.CLOSED:
        raise PeerIdentityError("closed peer cannot send messages")
    policy = receiver.inbound_policy
    if policy is PeerInboundPolicy.REFUSE:
        return PeerMessageStatus.REFUSED
    if policy is PeerInboundPolicy.HOLD:
        return PeerMessageStatus.HELD
    if policy is PeerInboundPolicy.ACCEPT:
        return PeerMessageStatus.QUEUED
    if sender.permission_mode == receiver.permission_mode:
        return PeerMessageStatus.QUEUED
    return PeerMessageStatus.HELD


def find_duplicate(
    connection: sqlite3.Connection,
    sender_instance_id: str,
    receiver_instance_id: str,
    content: str,
    dedupe_after: datetime,
) -> sqlite3.Row | None:
    digest = content_digest(content)
    return connection.execute(
        "SELECT * FROM peer_messages WHERE sender_instance_id = ? "
        "AND receiver_instance_id = ? AND content_sha256 = ? AND content = ? "
        "AND julianday(created_at) >= julianday(?) AND status <> 'expired' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (
            sender_instance_id, receiver_instance_id, digest, content,
            encode_datetime(dedupe_after),
        ),
    ).fetchone()


def enforce_send_rate(
    connection: sqlite3.Connection,
    sender_instance_id: str,
    rate_after: datetime,
    rate_limit: int,
) -> None:
    row = connection.execute(
        "SELECT COUNT(*) FROM peer_messages WHERE sender_instance_id = ? "
        "AND julianday(created_at) >= julianday(?)",
        (sender_instance_id, encode_datetime(rate_after)),
    ).fetchone()
    if int(row[0]) >= rate_limit:
        raise PeerRateLimitError("peer send rate exceeded")


def enforce_queue_capacity(
    connection: sqlite3.Connection,
    receiver_instance_id: str,
    status: PeerMessageStatus,
    queued_limit: int,
    held_limit: int,
) -> None:
    if status not in {PeerMessageStatus.QUEUED, PeerMessageStatus.HELD}:
        return
    limit = queued_limit if status is PeerMessageStatus.QUEUED else held_limit
    row = connection.execute(
        "SELECT COUNT(*) FROM peer_messages WHERE receiver_instance_id = ? "
        "AND status = ?",
        (receiver_instance_id, status.value),
    ).fetchone()
    if int(row[0]) >= limit:
        raise PeerQueueFullError(f"peer {status.value} inbox is full")


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
