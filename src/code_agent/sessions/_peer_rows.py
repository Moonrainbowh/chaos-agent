from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from code_agent.peers.errors import PeerIdentityError, PeerNotFoundError
from code_agent.peers.models import (
    PeerInboundPolicy,
    PeerMessage,
    PeerMessageStatus,
    PeerOrigin,
    PeerSession,
    PeerSessionStatus,
)

from ._codec import decode_datetime, encode_datetime
from .errors import SessionCorruptionError


def peer_session_from_row(row: sqlite3.Row) -> PeerSession:
    try:
        return PeerSession(
            instance_id=row["instance_id"],
            session_ref=row["session_ref"],
            name=row["name"],
            owner_pid=row["owner_pid"],
            owner_create_time=row["owner_create_time"],
            workspace_root=row["workspace_root"],
            thread_id=row["thread_id"],
            task_id=row["task_id"],
            permission_mode=row["permission_mode"],
            inbound_policy=PeerInboundPolicy(row["inbound_policy"]),
            status=PeerSessionStatus(row["status"]),
            heartbeat_at=decode_datetime(row["heartbeat_at"], "peer heartbeat"),
            created_at=decode_datetime(row["created_at"], "peer session"),
            updated_at=decode_datetime(row["updated_at"], "peer session"),
        )
    except SessionCorruptionError:
        raise
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted peer session") from error


def peer_message_from_row(row: sqlite3.Row) -> PeerMessage:
    try:
        return PeerMessage(
            id=row["id"],
            sender_instance_id=row["sender_instance_id"],
            receiver_instance_id=row["receiver_instance_id"],
            content=row["content"],
            status=PeerMessageStatus(row["status"]),
            origin=PeerOrigin(row["origin"]),
            claim_token=row["claim_token"],
            claimed_at=_optional_datetime(row["claimed_at"], "peer claim"),
            claim_expires_at=_optional_datetime(
                row["claim_expires_at"], "peer claim expiry"
            ),
            created_at=decode_datetime(row["created_at"], "peer message"),
            updated_at=decode_datetime(row["updated_at"], "peer message"),
            expires_at=decode_datetime(row["expires_at"], "peer message expiry"),
        )
    except SessionCorruptionError:
        raise
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted peer message") from error


def require_peer_owner(
    connection: sqlite3.Connection,
    instance_id: str,
    owner_pid: int,
    owner_create_time: float,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM peer_sessions WHERE instance_id = ?", (instance_id,)
    ).fetchone()
    if row is None:
        raise PeerNotFoundError("peer session not found")
    if int(row["owner_pid"]) != owner_pid or abs(
        float(row["owner_create_time"]) - owner_create_time
    ) >= 0.01:
        raise PeerIdentityError("peer process identity does not match")
    return row


def expire_peer_messages(
    connection: sqlite3.Connection, receiver_instance_id: str, now: datetime
) -> None:
    now = normalize_peer_time(now, "now")
    timestamp = encode_datetime(now)
    connection.execute(
        "UPDATE peer_messages SET status = 'expired', claim_token = NULL, "
        "claimed_at = NULL, claim_expires_at = NULL, updated_at = ? "
        "WHERE receiver_instance_id = ? AND status IN ('queued', 'held') "
        "AND julianday(expires_at) <= julianday(?)",
        (timestamp, receiver_instance_id, timestamp),
    )


def _optional_datetime(value: object, label: str) -> datetime | None:
    return None if value is None else decode_datetime(value, label)


def normalize_peer_time(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)
