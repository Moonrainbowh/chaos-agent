from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from code_agent.peers.models import (
    PeerMessageStatus,
    PeerOrigin,
    PeerSendResult,
)

from ._codec import encode_datetime
from ._peer_inbox import PeerInboxRepositoryMixin
from ._peer_message_ops import (
    content_digest,
    enforce_queue_capacity,
    enforce_send_rate,
    find_duplicate,
    initial_message_status,
    resolve_recipient,
    validate_limit,
    validate_message_text,
)
from ._peer_rows import (
    expire_peer_messages,
    normalize_peer_time,
    peer_message_from_row,
    require_peer_owner,
)
from ._records import _text


class PeerMessageRepositoryMixin(PeerInboxRepositoryMixin):
    _database: object

    async def enqueue_peer_message(
        self,
        *,
        sender_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        target: str,
        content: str,
        now: datetime,
        stale_before: datetime,
        queued_expires_at: datetime,
        held_expires_at: datetime,
        dedupe_after: datetime,
        rate_after: datetime,
        rate_limit: int,
        queued_limit: int,
        held_limit: int,
    ) -> PeerSendResult:
        sender_instance_id = _text(sender_instance_id, "sender_instance_id")
        target, content = _text(target, "target"), validate_message_text(content)
        for value, name in (
            (rate_limit, "rate_limit"), (queued_limit, "queued_limit"),
            (held_limit, "held_limit"),
        ):
            validate_limit(value, name)
        now = normalize_peer_time(now, "now")
        stale_before = normalize_peer_time(stale_before, "stale_before")
        queued_expires_at = normalize_peer_time(
            queued_expires_at, "queued_expires_at"
        )
        held_expires_at = normalize_peer_time(held_expires_at, "held_expires_at")
        dedupe_after = normalize_peer_time(dedupe_after, "dedupe_after")
        rate_after = normalize_peer_time(rate_after, "rate_after")
        if queued_expires_at <= now or held_expires_at <= now:
            raise ValueError("peer expiry must follow now")
        if stale_before > now or dedupe_after > now or rate_after > now:
            raise ValueError("peer lookback timestamps must not follow now")

        def write(connection: sqlite3.Connection) -> PeerSendResult:
            sender = require_peer_owner(
                connection, sender_instance_id, owner_pid, owner_create_time
            )
            recipient = resolve_recipient(
                connection, sender_instance_id, target, stale_before
            )
            receiver_id = recipient["instance_id"]
            expire_peer_messages(connection, receiver_id, now)
            duplicate = find_duplicate(
                connection, sender_instance_id, receiver_id, content, dedupe_after
            )
            if duplicate is not None:
                return PeerSendResult(peer_message_from_row(duplicate), True)
            enforce_send_rate(connection, sender_instance_id, rate_after, rate_limit)
            status = initial_message_status(sender, recipient)
            enforce_queue_capacity(
                connection, receiver_id, status, queued_limit, held_limit
            )
            identifier = uuid.uuid4().hex
            timestamp = encode_datetime(now)
            expires = held_expires_at if status is PeerMessageStatus.HELD else queued_expires_at
            connection.execute(
                "INSERT INTO peer_messages(id, sender_instance_id, "
                "receiver_instance_id, origin, content, content_sha256, status, "
                "claim_token, claimed_at, claim_expires_at, created_at, updated_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)",
                (
                    identifier, sender_instance_id, receiver_id, PeerOrigin.PEER.value,
                    content, content_digest(content), status.value, timestamp,
                    timestamp, encode_datetime(expires),
                ),
            )
            connection.execute(
                "UPDATE peer_sessions SET heartbeat_at = ?, updated_at = ? "
                "WHERE instance_id = ?",
                (timestamp, timestamp, sender_instance_id),
            )
            row = connection.execute(
                "SELECT * FROM peer_messages WHERE id = ?", (identifier,)
            ).fetchone()
            return PeerSendResult(peer_message_from_row(row), False)

        return await self._database.write(write)  # type: ignore[attr-defined]
