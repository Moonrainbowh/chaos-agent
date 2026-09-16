from __future__ import annotations

import sqlite3
import uuid
import hashlib
from collections.abc import Mapping

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import decode_datetime, decode_metadata, encode_datetime, encode_metadata, utc_now
from ._records import _text
from .errors import SessionNotFound
from .models import MemoryLifecycle, MemoryRecord


def assess_memory_applicability(record: MemoryRecord, context: Mapping[str, JSONValue]) -> str:
    """Compare declared conditions with current facts; never infer truth from provenance."""
    if not isinstance(record, MemoryRecord): raise TypeError("record must be a MemoryRecord")
    validate_json_mapping(context, "context")
    if record.lifecycle in {MemoryLifecycle.WITHDRAWN, MemoryLifecycle.SUPERSEDED, MemoryLifecycle.ARCHIVED}:
        return "not_applicable"
    if not record.conditions:
        return "needs_check"
    mismatches = [key for key, expected in record.conditions.items() if context.get(key) != expected]
    if not mismatches:
        return "applicable"
    known = [key for key in record.conditions if key in context]
    return "conflict" if known else "needs_check"


class MemoryRepositoryMixin:
    _database: object

    async def create_memory(self, scope_type: str, scope_id: str, kind: str, content: str, *, source_refs: Mapping[str, JSONValue] | None = None, origin: str = "unknown", conditions: Mapping[str, JSONValue] | None = None, lifecycle: MemoryLifecycle = MemoryLifecycle.CANDIDATE, memory_id: str | None = None, idempotency_key: str | None = None, allow_user_scope: bool = False, supersedes: str | None = None, derived_from: str | None = None) -> MemoryRecord:
        scope_type, scope_id, kind, content, origin = (_text(scope_type, "scope_type"), _text(scope_id, "scope_id"), _text(kind, "kind"), _text(content, "content"), _text(origin, "origin"))
        if scope_type not in {"task", "project", "user"}: raise ValueError("scope_type must be task, project, or user")
        if scope_type == "user" and not allow_user_scope: raise PermissionError("user-scope memory requires explicit authorization")
        if not isinstance(lifecycle, MemoryLifecycle): raise TypeError("lifecycle must be a MemoryLifecycle")
        refs, cond = ({} if source_refs is None else dict(source_refs)), ({} if conditions is None else dict(conditions)); validate_json_mapping(refs, "source_refs"); validate_json_mapping(cond, "conditions")
        identifier, timestamp = memory_id or uuid.uuid4().hex, encode_datetime(utc_now())
        def write(connection: sqlite3.Connection) -> MemoryRecord:
            if connection.execute("SELECT 1 FROM memory_forget WHERE scope_type=? AND scope_id=? AND content_sha256=?", (scope_type, scope_id, hashlib.sha256(content.encode("utf-8")).hexdigest())).fetchone() is not None:
                raise ValueError("memory content was explicitly forgotten")
            if idempotency_key:
                row = connection.execute("SELECT * FROM memories WHERE scope_type=? AND scope_id=? AND idempotency_key=?", (scope_type, scope_id, idempotency_key)).fetchone()
                if row is not None: return _memory_from_row(row)
            # Exact active entries are content-addressed at the scope boundary as
            # well as through an optional caller idempotency key.  This keeps
            # retries safe when a caller cannot persist its own request key,
            # while allowing the same text under different conditions to coexist.
            row = connection.execute(
                "SELECT * FROM memories WHERE scope_type=? AND scope_id=? AND kind=? AND content=? AND conditions=? AND lifecycle='active' ORDER BY revision DESC LIMIT 1",
                (scope_type, scope_id, kind, content, encode_metadata(cond)),
            ).fetchone()
            if row is not None: return _memory_from_row(row)
            connection.execute("INSERT INTO memories(memory_id,revision,scope_type,scope_id,kind,content,source_refs,origin,conditions,lifecycle,supersedes,derived_from,created_at,updated_at,idempotency_key) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, scope_type, scope_id, kind, content, encode_metadata(refs), origin, encode_metadata(cond), lifecycle.value, supersedes, derived_from, timestamp, timestamp, idempotency_key))
            if supersedes:
                connection.execute("UPDATE memories SET lifecycle='superseded', updated_at=? WHERE memory_id=? AND lifecycle IN ('candidate','active')", (timestamp, supersedes))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=1", (identifier,)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def revise_memory(self, memory_id: str, content: str, *, expected_revision: int, source_refs: Mapping[str, JSONValue] | None = None, conditions: Mapping[str, JSONValue] | None = None, lifecycle: MemoryLifecycle | None = None, idempotency_key: str | None = None) -> MemoryRecord:
        memory_id, content = _text(memory_id, "memory_id"), _text(content, "content")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1: raise ValueError("expected_revision must be positive")
        refs, cond = ({} if source_refs is None else dict(source_refs)), ({} if conditions is None else dict(conditions)); validate_json_mapping(refs, "source_refs"); validate_json_mapping(cond, "conditions")
        timestamp = encode_datetime(utc_now())
        def write(connection: sqlite3.Connection) -> MemoryRecord:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, expected_revision)).fetchone()
            if row is None: raise SessionNotFound("memory revision not found")
            latest = connection.execute("SELECT MAX(revision) FROM memories WHERE memory_id=?", (memory_id,)).fetchone()[0]
            if latest != expected_revision: raise ValueError("memory revision conflict; reload before revising")
            if idempotency_key:
                prior = connection.execute("SELECT * FROM memories WHERE scope_type=? AND scope_id=? AND idempotency_key=?", (row["scope_type"], row["scope_id"], idempotency_key)).fetchone()
                if prior is not None: return _memory_from_row(prior)
            current = MemoryLifecycle(row["lifecycle"]) if lifecycle is None else lifecycle
            connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (memory_id, expected_revision + 1, row["scope_type"], row["scope_id"], row["kind"], content, encode_metadata(refs), row["origin"], encode_metadata(cond), current.value, memory_id, row["memory_id"], row["created_at"], timestamp, idempotency_key))
            connection.execute("UPDATE memories SET lifecycle='superseded', updated_at=? WHERE memory_id=? AND revision=?", (timestamp, memory_id, expected_revision))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, expected_revision + 1)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def list_memories(self, scope_type: str, scope_id: str, *, include_history: bool = False, limit: int = 50) -> tuple[MemoryRecord, ...]:
        scope_type, scope_id = _text(scope_type, "scope_type"), _text(scope_id, "scope_id")
        if limit < 1 or limit > 200: raise ValueError("limit must be 1..200")
        def read(connection: sqlite3.Connection) -> tuple[MemoryRecord, ...]:
            query = "SELECT * FROM memories WHERE scope_type=? AND scope_id=?" + ("" if include_history else " AND lifecycle='active'") + " ORDER BY updated_at DESC, memory_id, revision DESC LIMIT ?"
            return tuple(_memory_from_row(row) for row in connection.execute(query, (scope_type, scope_id, limit)).fetchall())
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def promote_memory(self, memory_id: str, user_scope_id: str, content: str, *, allow_user_scope: bool = False, source_refs: Mapping[str, JSONValue] | None = None, conditions: Mapping[str, JSONValue] | None = None, lifecycle: MemoryLifecycle = MemoryLifecycle.CANDIDATE, idempotency_key: str | None = None) -> MemoryRecord:
        if not allow_user_scope: raise PermissionError("user-scope memory requires explicit authorization")
        memory_id, user_scope_id, content = _text(memory_id, "memory_id"), _text(user_scope_id, "user_scope_id"), _text(content, "content")
        def read(connection: sqlite3.Connection) -> tuple[str, str]:
            row = connection.execute("SELECT kind FROM memories WHERE memory_id=? AND lifecycle='active' ORDER BY revision DESC LIMIT 1", (memory_id,)).fetchone()
            if row is None: raise SessionNotFound("active source memory not found")
            return row["kind"], memory_id
        kind, source = await self._database.read(read)  # type: ignore[attr-defined]
        return await self.create_memory("user", user_scope_id, kind, content, source_refs=source_refs, conditions=conditions, lifecycle=lifecycle, allow_user_scope=True, idempotency_key=idempotency_key, derived_from=source)

    async def search_memories(self, project_id: str, query: str, *, user_scope_id: str | None = None, allow_user_scope: bool = False, limit: int = 20) -> tuple[MemoryRecord, ...]:
        """Return active lexical matches after scope filtering; user scope is opt-in."""
        project_id, query = _text(project_id, "project_id"), _text(query, "query")
        if limit < 1 or limit > 100: raise ValueError("limit must be 1..100")
        if user_scope_id is not None and not allow_user_scope: raise PermissionError("user-scope memory requires explicit authorization")
        scopes = [("project", project_id)]
        if user_scope_id is not None: scopes.append(("user", _text(user_scope_id, "user_scope_id")))
        needles = tuple(word for word in query.casefold().split() if len(word) > 1)
        def read(connection: sqlite3.Connection) -> tuple[MemoryRecord, ...]:
            found: list[MemoryRecord] = []
            for scope_type, scope_id in scopes:
                rows = connection.execute("SELECT * FROM memories WHERE scope_type=? AND scope_id=? AND lifecycle='active' ORDER BY updated_at DESC LIMIT ?", (scope_type, scope_id, limit)).fetchall()
                found.extend(_memory_from_row(row) for row in rows if any(word in row["content"].casefold() or word in row["kind"].casefold() for word in needles))
            return tuple(found[:limit])
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def search_memory_diagnostics(self, project_id: str, query: str, *, user_scope_id: str | None = None, allow_user_scope: bool = False, limit: int = 20) -> dict[str, object]:
        """Explain bounded lexical retrieval without exposing non-selected content."""
        project_id, query = _text(project_id, "project_id"), _text(query, "query")
        if user_scope_id is not None and not allow_user_scope: raise PermissionError("user-scope memory requires explicit authorization")
        scopes = [("project", project_id)]
        if user_scope_id is not None: scopes.append(("user", _text(user_scope_id, "user_scope_id")))
        words = tuple(word for word in query.casefold().split() if len(word) > 1)
        def read(connection: sqlite3.Connection) -> dict[str, object]:
            candidates, selected, excluded = 0, [], {"scope": 0, "lifecycle": 0, "query": 0}
            for scope_type, scope_id in scopes:
                rows = connection.execute("SELECT * FROM memories WHERE scope_type=? AND scope_id=? ORDER BY updated_at DESC LIMIT ?", (scope_type, scope_id, min(limit * 2, 100))).fetchall()
                for row in rows:
                    candidates += 1
                    if row["lifecycle"] != "active": excluded["lifecycle"] += 1; continue
                    if not any(word in row["content"].casefold() or word in row["kind"].casefold() for word in words): excluded["query"] += 1; continue
                    if len(selected) < limit: selected.append(row["memory_id"] + "@" + str(row["revision"]))
            return {"allowed_scopes": tuple(f"{kind}:{sid}" for kind, sid in scopes), "candidate_count": candidates, "selected": tuple(selected), "excluded": excluded, "query_token_estimate": len(words)}
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def set_memory_lifecycle(self, memory_id: str, *, revision: int, lifecycle: MemoryLifecycle) -> MemoryRecord:
        memory_id = _text(memory_id, "memory_id")
        if not isinstance(lifecycle, MemoryLifecycle): raise TypeError("lifecycle must be a MemoryLifecycle")
        timestamp = encode_datetime(utc_now())
        def write(connection: sqlite3.Connection) -> MemoryRecord:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, revision)).fetchone()
            if row is None: raise SessionNotFound("memory revision not found")
            latest = connection.execute("SELECT MAX(revision) FROM memories WHERE memory_id=?", (memory_id,)).fetchone()[0]
            if latest != revision: raise ValueError("memory revision conflict; reload before changing lifecycle")
            connection.execute("UPDATE memories SET lifecycle=?, updated_at=? WHERE memory_id=? AND revision=?", (lifecycle.value, timestamp, memory_id, revision))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, revision)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def delete_memory(self, memory_id: str) -> None:
        memory_id = _text(memory_id, "memory_id")
        def write(connection: sqlite3.Connection) -> None:
            rows = connection.execute("SELECT scope_type, scope_id, content FROM memories WHERE memory_id=?", (memory_id,)).fetchall()
            if not rows: raise SessionNotFound("memory not found")
            timestamp = encode_datetime(utc_now())
            for row in rows:
                connection.execute("INSERT OR IGNORE INTO memory_forget VALUES (?,?,?,?)", (row["scope_type"], row["scope_id"], hashlib.sha256(row["content"].encode("utf-8")).hexdigest(), timestamp))
            connection.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
        await self._database.write(write)  # type: ignore[attr-defined]


def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(row["memory_id"], int(row["revision"]), row["scope_type"], row["scope_id"], row["kind"], row["content"], decode_metadata(row["source_refs"]), row["origin"], decode_metadata(row["conditions"]), MemoryLifecycle(row["lifecycle"]), row["supersedes"], row["derived_from"], decode_datetime(row["created_at"], "memory"), decode_datetime(row["updated_at"], "memory"))
