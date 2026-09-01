from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from code_agent.peers.errors import PeerClaimConflictError
from code_agent.peers.models import PeerClaim, PeerMessage, PeerMessageStatus

from ._codec import encode_datetime
from ._peer_message_ops import enforce_queue_capacity, validate_limit
from ._peer_rows import (
    expire_peer_messages,
    normalize_peer_time,
    peer_message_from_row,
    require_peer_owner,
)
from ._records import _text


class PeerInboxRepositoryMixin:
    _database: object

    async def list_peer_inbox(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        statuses: tuple[PeerMessageStatus, ...],
        now: datetime,
        limit: int,
    ) -> tuple[PeerMessage, ...]:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        limit = validate_limit(limit, "limit", 1_000)
        if not statuses or any(not isinstance(item, PeerMessageStatus) for item in statuses):
            raise TypeError("statuses must contain PeerMessageStatus values")
        values = tuple(dict.fromkeys(item.value for item in statuses))
        now = normalize_peer_time(now, "now")

        def write(connection: sqlite3.Connection) -> tuple[PeerMessage, ...]:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            placeholders = ",".join("?" for _ in values)
            rows = connection.execute(
                f"SELECT * FROM peer_messages WHERE receiver_instance_id = ? "
                f"AND status IN ({placeholders}) ORDER BY created_at, id LIMIT ?",
                (receiver_instance_id, *values, limit),
            ).fetchall()
            return tuple(peer_message_from_row(row) for row in rows)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def claim_peer_messages(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        now: datetime,
        claim_expires_at: datetime,
        limit: int,
    ) -> tuple[PeerClaim, ...]:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        limit = validate_limit(limit, "limit", 100)
        now = normalize_peer_time(now, "now")
        claim_expires_at = normalize_peer_time(
            claim_expires_at, "claim_expires_at"
        )
        timestamp, claim_expiry = encode_datetime(now), encode_datetime(claim_expires_at)
        if claim_expires_at <= now:
            raise ValueError("claim_expires_at must follow now")

        def write(connection: sqlite3.Connection) -> tuple[PeerClaim, ...]:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            connection.execute(
                "UPDATE peer_messages SET claim_token = NULL, claimed_at = NULL, "
                "claim_expires_at = NULL, updated_at = ? WHERE receiver_instance_id = ? "
                "AND status = 'queued' "
                "AND julianday(claim_expires_at) <= julianday(?)",
                (timestamp, receiver_instance_id, timestamp),
            )
            rows = connection.execute(
                "SELECT id FROM peer_messages WHERE receiver_instance_id = ? "
                "AND status = 'queued' AND claim_token IS NULL "
                "ORDER BY created_at, id LIMIT ?",
                (receiver_instance_id, limit),
            ).fetchall()
            claims: list[PeerClaim] = []
            for row in rows:
                token = uuid.uuid4().hex
                cursor = connection.execute(
                    "UPDATE peer_messages SET claim_token = ?, claimed_at = ?, "
                    "claim_expires_at = ?, updated_at = ? WHERE id = ? "
                    "AND receiver_instance_id = ? AND status = 'queued' "
                    "AND claim_token IS NULL",
                    (
                        token, timestamp, claim_expiry, timestamp, row["id"],
                        receiver_instance_id,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                claimed = connection.execute(
                    "SELECT * FROM peer_messages WHERE id = ?", (row["id"],)
                ).fetchone()
                claims.append(PeerClaim(peer_message_from_row(claimed), token))
            return tuple(claims)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def renew_peer_message_claim(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        message_id: str,
        claim_token: str,
        now: datetime,
        claim_expires_at: datetime,
    ) -> PeerClaim:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        message_id = _text(message_id, "message_id")
        claim_token = _text(claim_token, "claim_token")
        now = normalize_peer_time(now, "now")
        claim_expires_at = normalize_peer_time(
            claim_expires_at, "claim_expires_at"
        )
        if claim_expires_at <= now:
            raise ValueError("claim_expires_at must follow now")
        timestamp, claim_expiry = encode_datetime(now), encode_datetime(
            claim_expires_at
        )

        def write(connection: sqlite3.Connection) -> PeerClaim:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            cursor = connection.execute(
                "UPDATE peer_messages SET claim_expires_at = ?, updated_at = ? "
                "WHERE id = ? AND receiver_instance_id = ? AND status = 'queued' "
                "AND claim_token = ? AND julianday(expires_at) > julianday(?)",
                (
                    claim_expiry,
                    timestamp,
                    message_id,
                    receiver_instance_id,
                    claim_token,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise PeerClaimConflictError("peer claim no longer matches")
            row = connection.execute(
                "SELECT * FROM peer_messages WHERE id = ?", (message_id,)
            ).fetchone()
            return PeerClaim(peer_message_from_row(row), claim_token)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def get_peer_message(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        message_id: str,
        now: datetime,
    ) -> PeerMessage:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        message_id = _text(message_id, "message_id")
        now = normalize_peer_time(now, "now")

        def write(connection: sqlite3.Connection) -> PeerMessage:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            row = connection.execute(
                "SELECT * FROM peer_messages WHERE id = ? "
                "AND receiver_instance_id = ?",
                (message_id, receiver_instance_id),
            ).fetchone()
            if row is None:
                raise PeerClaimConflictError("peer message is unavailable")
            return peer_message_from_row(row)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def resolve_held_peer_message(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        message_id: str,
        accept: bool,
        now: datetime,
        accepted_expires_at: datetime,
        queued_limit: int,
    ) -> PeerMessage:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        message_id = _text(message_id, "message_id")
        if not isinstance(accept, bool):
            raise TypeError("accept must be a bool")
        queued_limit = validate_limit(queued_limit, "queued_limit")
        now = normalize_peer_time(now, "now")
        accepted_expires_at = normalize_peer_time(
            accepted_expires_at, "accepted_expires_at"
        )
        if accept and accepted_expires_at <= now:
            raise ValueError("accepted_expires_at must follow now")
        timestamp = encode_datetime(now)

        def write(connection: sqlite3.Connection) -> PeerMessage:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            current = connection.execute(
                "SELECT status FROM peer_messages WHERE id = ? "
                "AND receiver_instance_id = ?",
                (message_id, receiver_instance_id),
            ).fetchone()
            if current is None or current["status"] != PeerMessageStatus.HELD.value:
                raise PeerClaimConflictError("held message state no longer matches")
            if accept:
                enforce_queue_capacity(
                    connection, receiver_instance_id, PeerMessageStatus.QUEUED,
                    queued_limit, 1,
                )
            status = (
                PeerMessageStatus.QUEUED if accept else PeerMessageStatus.REFUSED
            )
            expires = encode_datetime(accepted_expires_at) if accept else None
            cursor = connection.execute(
                "UPDATE peer_messages SET status = ?, expires_at = COALESCE(?, expires_at), "
                "updated_at = ? WHERE id = ? AND receiver_instance_id = ? "
                "AND status = 'held'",
                (status.value, expires, timestamp, message_id, receiver_instance_id),
            )
            if cursor.rowcount != 1:
                raise PeerClaimConflictError("held message state no longer matches")
            row = connection.execute(
                "SELECT * FROM peer_messages WHERE id = ?", (message_id,)
            ).fetchone()
            return peer_message_from_row(row)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def acknowledge_peer_message(
        self,
        receiver_instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        message_id: str,
        claim_token: str,
        outcome: PeerMessageStatus,
        now: datetime,
    ) -> PeerMessage:
        receiver_instance_id = _text(receiver_instance_id, "receiver_instance_id")
        message_id, claim_token = _text(message_id, "message_id"), _text(
            claim_token, "claim_token"
        )
        if outcome not in {PeerMessageStatus.DELIVERED, PeerMessageStatus.REFUSED}:
            raise ValueError("outcome must be delivered or refused")
        now = normalize_peer_time(now, "now")
        timestamp = encode_datetime(now)

        def write(connection: sqlite3.Connection) -> PeerMessage:
            require_peer_owner(
                connection, receiver_instance_id, owner_pid, owner_create_time
            )
            expire_peer_messages(connection, receiver_instance_id, now)
            cursor = connection.execute(
                "UPDATE peer_messages SET status = ?, claim_token = NULL, "
                "claimed_at = NULL, claim_expires_at = NULL, updated_at = ? "
                "WHERE id = ? AND receiver_instance_id = ? AND status = 'queued' "
                "AND claim_token = ? "
                "AND julianday(claim_expires_at) > julianday(?)",
                (
                    outcome.value, timestamp, message_id, receiver_instance_id,
                    claim_token, timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise PeerClaimConflictError("peer claim no longer matches")
            row = connection.execute(
                "SELECT * FROM peer_messages WHERE id = ?", (message_id,)
            ).fetchone()
            return peer_message_from_row(row)

        return await self._database.write(write)  # type: ignore[attr-defined]
