from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from code_agent.core.models import Usage
from code_agent.thread_intelligence.models import (
    EntryRelation,
    SearchHit,
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    ThreadEntry,
)

from ._codec import encode_datetime, utc_now
from ._database import SessionDatabase
from ._records import _require_thread, _text
from .errors import SessionCorruptionError


class SemanticRepositoryMixin:
    _database: SessionDatabase

    async def publish_semantic_checkpoint(
        self,
        checkpoint: SemanticCheckpoint,
        entries: Iterable[ThreadEntry],
    ) -> None:
        if not isinstance(checkpoint, SemanticCheckpoint):
            raise TypeError("checkpoint must be a SemanticCheckpoint")
        checked = tuple(entries)
        if any(not isinstance(entry, ThreadEntry) for entry in checked):
            raise TypeError("entries must contain ThreadEntry values")
        if any(entry.anchor.thread_id != checkpoint.thread_id for entry in checked):
            raise ValueError("index entries must belong to the checkpoint thread")
        checkpoint_payload = _checkpoint_payload(checkpoint)
        entry_payloads = tuple((entry, _entry_payload(entry)) for entry in checked)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, checkpoint.thread_id)
            _insert_immutable(
                connection,
                "semantic_checkpoints",
                "id",
                checkpoint.id,
                checkpoint_payload,
                (
                    checkpoint.id,
                    checkpoint.thread_id,
                    checkpoint_payload,
                    timestamp,
                ),
                "INSERT INTO semantic_checkpoints(id, thread_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
            )
            for entry, payload in entry_payloads:
                _insert_immutable(
                    connection,
                    "thread_index_entries",
                    "stable_id",
                    entry.anchor.stable_id,
                    payload,
                    (
                        entry.anchor.stable_id,
                        checkpoint.id,
                        checkpoint.thread_id,
                        entry.anchor.sequence,
                        entry.text,
                        payload,
                        timestamp,
                    ),
                    "INSERT INTO thread_index_entries("
                    "stable_id, checkpoint_id, thread_id, sequence, text, payload, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                )

        await self._database.write(write)

    async def load_semantic_checkpoints(
        self, thread_id: str
    ) -> tuple[SemanticCheckpoint, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[SemanticCheckpoint, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM semantic_checkpoints WHERE thread_id = ? "
                "ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
            return tuple(_decode_checkpoint(row["payload"]) for row in rows)

        return await self._database.read(read)

    async def search_thread_index(
        self, thread_ids: Iterable[str], query: str, *, limit: int = 20
    ) -> tuple[SearchHit, ...]:
        selected = tuple(dict.fromkeys(_text(value, "thread_id") for value in thread_ids))
        query = _text(query, "query")
        if not selected:
            return ()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        def read(connection: sqlite3.Connection) -> tuple[SearchHit, ...]:
            for thread_id in selected:
                _require_thread(connection, thread_id)
            placeholders = ",".join("?" for _ in selected)
            rows = connection.execute(
                "SELECT payload FROM thread_index_entries "
                f"WHERE thread_id IN ({placeholders}) AND instr(lower(text), lower(?)) > 0 "
                "ORDER BY sequence DESC, stable_id LIMIT ?",
                (*selected, query, limit),
            ).fetchall()
            return tuple(SearchHit(_decode_entry(row["payload"]), 1) for row in rows)

        return await self._database.read(read)

    async def read_thread_entry(self, anchor: SourceAnchor) -> ThreadEntry:
        if not isinstance(anchor, SourceAnchor):
            raise TypeError("anchor must be a SourceAnchor")

        def read(connection: sqlite3.Connection) -> ThreadEntry:
            row = connection.execute(
                "SELECT payload FROM thread_index_entries WHERE stable_id = ?",
                (anchor.stable_id,),
            ).fetchone()
            if row is None:
                raise KeyError("thread index entry not found")
            entry = _decode_entry(row["payload"])
            if entry.anchor != anchor:
                raise KeyError("source anchor does not match persisted entry")
            return entry

        return await self._database.read(read)

    async def load_thread_entries(
        self, thread_ids: Iterable[str]
    ) -> tuple[ThreadEntry, ...]:
        selected = tuple(dict.fromkeys(_text(value, "thread_id") for value in thread_ids))
        if not selected:
            return ()

        def read(connection: sqlite3.Connection) -> tuple[ThreadEntry, ...]:
            for thread_id in selected:
                _require_thread(connection, thread_id)
            placeholders = ",".join("?" for _ in selected)
            rows = connection.execute(
                "SELECT payload FROM thread_index_entries "
                f"WHERE thread_id IN ({placeholders}) "
                "ORDER BY sequence, stable_id",
                selected,
            ).fetchall()
            return tuple(_decode_entry(row["payload"]) for row in rows)

        return await self._database.read(read)


def _insert_immutable(
    connection: sqlite3.Connection,
    table: str,
    key_column: str,
    key: str,
    payload: str,
    values: tuple[object, ...],
    statement: str,
) -> None:
    row = connection.execute(
        f"SELECT payload FROM {table} WHERE {key_column} = ?", (key,)
    ).fetchone()
    if row is None:
        connection.execute(statement, values)
    elif row["payload"] != payload:
        raise ValueError("stable semantic identity already has different content")


def _checkpoint_payload(checkpoint: SemanticCheckpoint) -> str:
    return _json(
        {
            "id": checkpoint.id,
            "thread_id": checkpoint.thread_id,
            "source_start": _anchor_dict(checkpoint.source_start),
            "source_end": _anchor_dict(checkpoint.source_end),
            "source_digest": checkpoint.source_digest,
            "summary": checkpoint.summary,
            "model": checkpoint.model,
            "usage": checkpoint.usage.to_dict(),
            "version": checkpoint.version,
        }
    )


def _entry_payload(entry: ThreadEntry) -> str:
    return _json(
        {
            "anchor": _anchor_dict(entry.anchor),
            "text": entry.text,
            "relation": entry.relation.value,
            "related_id": entry.related_id,
            "tool_call_id": entry.tool_call_id,
            "tool_is_error": entry.tool_is_error,
        }
    )


def _anchor_dict(anchor: SourceAnchor) -> dict[str, object]:
    return {
        "thread_id": anchor.thread_id,
        "kind": anchor.kind.value,
        "sequence": anchor.sequence,
        "stable_id": anchor.stable_id,
        "digest": anchor.digest,
    }


def _decode_anchor(value: object) -> SourceAnchor:
    if not isinstance(value, dict):
        raise ValueError("invalid source anchor")
    return SourceAnchor(
        value["thread_id"],
        SourceKind(value["kind"]),
        value["sequence"],
        value["stable_id"],
        value["digest"],
    )


def _decode_checkpoint(payload: str) -> SemanticCheckpoint:
    try:
        value = json.loads(payload)
        return SemanticCheckpoint(
            value["id"],
            value["thread_id"],
            _decode_anchor(value["source_start"]),
            _decode_anchor(value["source_end"]),
            value["source_digest"],
            value["summary"],
            value["model"],
            Usage.from_dict(value["usage"]),
            value["version"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid semantic checkpoint") from error


def _decode_entry(payload: str) -> ThreadEntry:
    try:
        value = json.loads(payload)
        return ThreadEntry(
            _decode_anchor(value["anchor"]),
            value["text"],
            EntryRelation(value["relation"]),
            value["related_id"],
            value["tool_call_id"],
            value["tool_is_error"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid thread index entry") from error


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
