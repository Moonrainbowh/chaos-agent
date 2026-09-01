from __future__ import annotations

import sqlite3
from datetime import datetime

from code_agent.peers.errors import PeerIdentityError, PeerNotFoundError
from code_agent.peers.models import PeerSession, PeerSessionStatus

from ._codec import decode_datetime, encode_datetime
from ._peer_rows import normalize_peer_time, peer_session_from_row, require_peer_owner
from ._records import _text


class PeerSessionRepositoryMixin:
    _database: object

    async def register_peer_session(self, session: PeerSession) -> PeerSession:
        if not isinstance(session, PeerSession):
            raise TypeError("session must be a PeerSession")

        def write(connection: sqlite3.Connection) -> PeerSession:
            _validate_context_refs(connection, session.thread_id, session.task_id)
            collision = connection.execute(
                "SELECT instance_id FROM peer_sessions "
                "WHERE session_ref = ? COLLATE NOCASE AND instance_id <> ?",
                (session.session_ref, session.instance_id),
            ).fetchone()
            if collision is not None:
                raise PeerIdentityError("peer session ref is already registered")
            existing = connection.execute(
                "SELECT * FROM peer_sessions WHERE instance_id = ?",
                (session.instance_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO peer_sessions(instance_id, session_ref, name, "
                    "owner_pid, owner_create_time, workspace_root, thread_id, "
                    "task_id, permission_mode, inbound_policy, status, heartbeat_at, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _session_values(session),
                )
            else:
                require_peer_owner(
                    connection,
                    session.instance_id,
                    session.owner_pid,
                    session.owner_create_time,
                )
                if str(existing["session_ref"]).casefold() != session.session_ref.casefold():
                    raise PeerIdentityError("peer session ref cannot change")
                if (
                    existing["status"] == PeerSessionStatus.CLOSED.value
                    and session.status is not PeerSessionStatus.CLOSED
                ):
                    raise PeerIdentityError("closed peer session cannot become live")
                if session.heartbeat_at < decode_datetime(
                    existing["heartbeat_at"], "peer heartbeat"
                ):
                    raise PeerIdentityError("peer heartbeat must be monotonic")
                connection.execute(
                    "UPDATE peer_sessions SET name = ?, workspace_root = ?, "
                    "thread_id = ?, task_id = ?, permission_mode = ?, "
                    "inbound_policy = ?, status = ?, heartbeat_at = ?, updated_at = ? "
                    "WHERE instance_id = ?",
                    (
                        session.name, session.workspace_root, session.thread_id,
                        session.task_id, session.permission_mode,
                        session.inbound_policy.value, session.status.value,
                        encode_datetime(session.heartbeat_at),
                        encode_datetime(session.updated_at), session.instance_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM peer_sessions WHERE instance_id = ?",
                (session.instance_id,),
            ).fetchone()
            return peer_session_from_row(row)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def rename_peer_session(
        self,
        instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        name: str,
        now: datetime,
    ) -> PeerSession:
        instance_id, name = _text(instance_id, "instance_id"), _text(name, "name")
        timestamp = encode_datetime(normalize_peer_time(now, "now"))

        def write(connection: sqlite3.Connection) -> PeerSession:
            require_peer_owner(connection, instance_id, owner_pid, owner_create_time)
            connection.execute(
                "UPDATE peer_sessions SET name = ?, heartbeat_at = ?, updated_at = ? "
                "WHERE instance_id = ?",
                (name, timestamp, timestamp, instance_id),
            )
            return peer_session_from_row(
                connection.execute(
                    "SELECT * FROM peer_sessions WHERE instance_id = ?", (instance_id,)
                ).fetchone()
            )

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def close_peer_session(
        self,
        instance_id: str,
        owner_pid: int,
        owner_create_time: float,
        now: datetime,
    ) -> PeerSession:
        instance_id = _text(instance_id, "instance_id")
        timestamp = encode_datetime(normalize_peer_time(now, "now"))

        def write(connection: sqlite3.Connection) -> PeerSession:
            require_peer_owner(connection, instance_id, owner_pid, owner_create_time)
            connection.execute(
                "UPDATE peer_sessions SET status = ?, heartbeat_at = ?, updated_at = ? "
                "WHERE instance_id = ?",
                (PeerSessionStatus.CLOSED.value, timestamp, timestamp, instance_id),
            )
            connection.execute(
                "UPDATE peer_messages SET status = 'expired', claim_token = NULL, "
                "claimed_at = NULL, claim_expires_at = NULL, updated_at = ? "
                "WHERE receiver_instance_id = ? AND status IN ('queued', 'held')",
                (timestamp, instance_id),
            )
            return peer_session_from_row(
                connection.execute(
                    "SELECT * FROM peer_sessions WHERE instance_id = ?", (instance_id,)
                ).fetchone()
            )

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def list_peer_sessions(
        self,
        stale_before: datetime,
        *,
        exclude_instance_id: str | None = None,
    ) -> tuple[PeerSession, ...]:
        if exclude_instance_id is not None:
            exclude_instance_id = _text(exclude_instance_id, "exclude_instance_id")
        cutoff = encode_datetime(normalize_peer_time(stale_before, "stale_before"))

        def read(connection: sqlite3.Connection) -> tuple[PeerSession, ...]:
            rows = connection.execute(
                "SELECT * FROM peer_sessions WHERE status <> 'closed' "
                "AND julianday(heartbeat_at) >= julianday(?) "
                "AND (? IS NULL OR instance_id <> ?) "
                "ORDER BY name COLLATE NOCASE, session_ref COLLATE NOCASE",
                (cutoff, exclude_instance_id, exclude_instance_id),
            ).fetchall()
            return tuple(peer_session_from_row(row) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]


def _session_values(session: PeerSession) -> tuple[object, ...]:
    return (
        session.instance_id, session.session_ref, session.name, session.owner_pid,
        session.owner_create_time, session.workspace_root, session.thread_id,
        session.task_id, session.permission_mode, session.inbound_policy.value,
        session.status.value, encode_datetime(session.heartbeat_at),
        encode_datetime(session.created_at), encode_datetime(session.updated_at),
    )


def _validate_context_refs(
    connection: sqlite3.Connection, thread_id: str | None, task_id: str | None
) -> None:
    if thread_id is not None and connection.execute(
        "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
    ).fetchone() is None:
        raise PeerNotFoundError("peer thread not found")
    if task_id is None:
        return
    row = connection.execute(
        "SELECT thread_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise PeerNotFoundError("peer task not found")
    if thread_id is not None and row["thread_id"] != thread_id:
        raise PeerIdentityError("peer task does not belong to peer thread")
