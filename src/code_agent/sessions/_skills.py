from __future__ import annotations

import sqlite3

from ._codec import decode_datetime, encode_datetime, utc_now
from ._database import SessionDatabase
from ._records import _require_thread, _text
from .models import SkillActivationRecord


class SkillActivationRepositoryMixin:
    _database: SessionDatabase

    async def save_skill_activation(
        self, thread_id: str, skill_id: str, source: str, digest: str
    ) -> SkillActivationRecord:
        record = SkillActivationRecord(
            _text(thread_id, "thread_id"),
            _text(skill_id, "skill_id"),
            _text(source, "source"),
            _text(digest, "digest"),
            utc_now(),
        )

        def write(connection: sqlite3.Connection) -> SkillActivationRecord:
            _require_thread(connection, record.thread_id)
            connection.execute(
                "INSERT INTO skill_activations(thread_id, skill_id, source, digest, activated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(thread_id, skill_id) DO UPDATE SET "
                "source=excluded.source, digest=excluded.digest, activated_at=excluded.activated_at",
                (
                    record.thread_id,
                    record.skill_id,
                    record.source,
                    record.digest,
                    encode_datetime(record.activated_at),
                ),
            )
            return record

        return await self._database.write(write)

    async def list_skill_activations(
        self, thread_id: str
    ) -> tuple[SkillActivationRecord, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[SkillActivationRecord, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT skill_id, source, digest, activated_at FROM skill_activations "
                "WHERE thread_id = ? ORDER BY activated_at, skill_id",
                (thread_id,),
            ).fetchall()
            return tuple(
                SkillActivationRecord(
                    thread_id,
                    row["skill_id"],
                    row["source"],
                    row["digest"],
                    decode_datetime(row["activated_at"], "skill activation"),
                )
                for row in rows
            )

        return await self._database.read(read)

    async def remove_skill_activation(
        self, thread_id: str, skill_id: str
    ) -> bool:
        thread_id = _text(thread_id, "thread_id")
        skill_id = _text(skill_id, "skill_id")

        def write(connection: sqlite3.Connection) -> bool:
            _require_thread(connection, thread_id)
            cursor = connection.execute(
                "DELETE FROM skill_activations WHERE thread_id = ? AND skill_id = ?",
                (thread_id, skill_id),
            )
            return cursor.rowcount == 1

        return await self._database.write(write)
