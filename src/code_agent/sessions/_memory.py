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
from ._memory_search import bounded_rows, condition_status, search_plan, validate_page


def assess_memory_applicability(record: MemoryRecord, context: Mapping[str, JSONValue]) -> str:
    """Compare declared conditions with current facts; never infer truth from provenance."""
    if not isinstance(record, MemoryRecord): raise TypeError("record must be a MemoryRecord")
    validate_json_mapping(context, "context")
    if record.lifecycle in {MemoryLifecycle.WITHDRAWN, MemoryLifecycle.SUPERSEDED, MemoryLifecycle.ARCHIVED}:
        return "not_applicable"
    return condition_status(record.conditions, context)


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
            _check_forgotten(connection, scope_type, scope_id, "memory-id\0" + identifier, domain_id=True)
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
                prior = connection.execute("SELECT scope_type,scope_id FROM memories WHERE memory_id=? ORDER BY revision DESC LIMIT 1", (supersedes,)).fetchone()
                if prior is None: raise SessionNotFound("superseded memory not found")
                _check_scope(prior, scope_type, scope_id)
                connection.execute("UPDATE memories SET lifecycle='superseded', updated_at=? WHERE memory_id=? AND lifecycle IN ('candidate','active')", (timestamp, supersedes))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=1", (identifier,)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def revise_memory(self, memory_id: str, content: str, *, expected_revision: int, source_refs: Mapping[str, JSONValue] | None = None, conditions: Mapping[str, JSONValue] | None = None, lifecycle: MemoryLifecycle | None = None, idempotency_key: str | None = None, scope_type: str | None = None, scope_id: str | None = None) -> MemoryRecord:
        memory_id, content = _text(memory_id, "memory_id"), _text(content, "content")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1: raise ValueError("expected_revision must be positive")
        refs, cond = ({} if source_refs is None else dict(source_refs)), ({} if conditions is None else dict(conditions)); validate_json_mapping(refs, "source_refs"); validate_json_mapping(cond, "conditions")
        timestamp = encode_datetime(utc_now())
        def write(connection: sqlite3.Connection) -> MemoryRecord:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, expected_revision)).fetchone()
            if row is None: raise SessionNotFound("memory revision not found")
            _check_scope(row, scope_type, scope_id)
            latest = connection.execute("SELECT MAX(revision) FROM memories WHERE memory_id=?", (memory_id,)).fetchone()[0]
            if latest != expected_revision: raise ValueError("memory revision conflict; reload before revising")
            if idempotency_key:
                prior = connection.execute("SELECT * FROM memories WHERE scope_type=? AND scope_id=? AND idempotency_key=?", (row["scope_type"], row["scope_id"], idempotency_key)).fetchone()
                if prior is not None: return _memory_from_row(prior)
            _check_forgotten(connection, row["scope_type"], row["scope_id"], content)
            current = MemoryLifecycle(row["lifecycle"]) if lifecycle is None else lifecycle
            if not isinstance(current, MemoryLifecycle): raise TypeError("lifecycle must be a MemoryLifecycle")
            saved_refs = row["source_refs"] if source_refs is None else encode_metadata(refs)
            saved_conditions = row["conditions"] if conditions is None else encode_metadata(cond)
            connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (memory_id, expected_revision + 1, row["scope_type"], row["scope_id"], row["kind"], content, saved_refs, row["origin"], saved_conditions, current.value, memory_id, row["memory_id"], row["created_at"], timestamp, idempotency_key))
            connection.execute("UPDATE memories SET lifecycle='superseded', updated_at=? WHERE memory_id=? AND revision=?", (timestamp, memory_id, expected_revision))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, expected_revision + 1)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def list_memories(self, scope_type: str, scope_id: str, *, include_history: bool = False, limit: int = 50, offset: int = 0, max_bytes: int = 1024 * 1024) -> tuple[MemoryRecord, ...]:
        scope_type, scope_id = _text(scope_type, "scope_type"), _text(scope_id, "scope_id")
        validate_page(limit, offset, max_bytes, 200)
        def read(connection: sqlite3.Connection) -> tuple[MemoryRecord, ...]:
            where = "m.scope_type=? AND m.scope_id=?"
            if not include_history: where += " AND m.lifecycle='active' AND m.revision=(SELECT MAX(n.revision) FROM memories n WHERE n.memory_id=m.memory_id)"
            return tuple(_memory_from_row(row) for row in bounded_rows(connection, where, (scope_type, scope_id), order="m.updated_at DESC,m.memory_id,m.revision DESC", limit=limit, offset=offset, max_bytes=max_bytes))
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

    async def search_memories(self, project_id: str, query: str, *, user_scope_id: str | None = None, allow_user_scope: bool = False, limit: int = 20, offset: int = 0, max_bytes: int = 1024 * 1024, context: Mapping[str, JSONValue] | None = None, applicability: tuple[str, ...] | None = None, require_applicable: bool = False) -> tuple[MemoryRecord, ...]:
        """Filter scope, latest active revision and applicability before lexical ranking."""
        project_id, query = _text(project_id, "project_id"), _text(query, "query")
        validate_page(limit, offset, max_bytes)
        if context is not None: validate_json_mapping(context, "context")
        scopes = _search_scopes(project_id, user_scope_id, allow_user_scope)
        def read(connection: sqlite3.Connection) -> tuple[MemoryRecord, ...]:
            where, params, _, _, _ = search_plan(connection, scopes, query, context, applicability, max_bytes, require_applicable)
            return tuple(_memory_from_row(row) for row in bounded_rows(connection, where, params, order="memory_score(m.content,m.kind) DESC,m.updated_at DESC,m.memory_id,m.revision DESC", limit=limit, offset=offset, max_bytes=max_bytes))
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def search_memory_diagnostics(self, project_id: str, query: str, *, user_scope_id: str | None = None, allow_user_scope: bool = False, limit: int = 20, offset: int = 0, max_bytes: int = 1024 * 1024, context: Mapping[str, JSONValue] | None = None, applicability: tuple[str, ...] | None = None, require_applicable: bool = False) -> dict[str, object]:
        """Use the same retrieval plan; count scoped exclusions without returning bodies."""
        project_id, query = _text(project_id, "project_id"), _text(query, "query")
        validate_page(limit, offset, max_bytes)
        if context is not None: validate_json_mapping(context, "context")
        scopes = _search_scopes(project_id, user_scope_id, allow_user_scope)
        def read(connection: sqlite3.Connection) -> dict[str, object]:
            where, params, base, eligible, terms = search_plan(connection, scopes, query, context, applicability, max_bytes, require_applicable)
            scope_params = [value for scope in scopes for value in scope]
            count = lambda clause, values: connection.execute("SELECT COUNT(*) FROM memories m WHERE " + clause, values).fetchone()[0]
            candidates = count(base, scope_params)
            active = count(base + " AND m.lifecycle='active'", scope_params)
            applicable = count(eligible, params[:-1])
            matches = count(where, params)
            rows = bounded_rows(connection, where, params, order="memory_score(m.content,m.kind) DESC,m.updated_at DESC,m.memory_id,m.revision DESC", limit=limit, offset=offset, max_bytes=max_bytes)
            return {"allowed_scopes": tuple(f"{kind}:{sid}" for kind, sid in scopes), "candidate_count": candidates, "match_count": matches, "selected": tuple(row["memory_id"] + "@" + str(row["revision"]) for row in rows), "excluded": {"scope": 0, "lifecycle": candidates-active, "applicability": active-applicable, "query": applicable-matches}, "query_token_estimate": len(terms)}
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def get_memory(self, memory_id: str, *, scope_type: str, scope_id: str, revision: int | None = None, max_bytes: int = 1024 * 1024) -> MemoryRecord:
        """Read one item only within its explicit scope, including inactive history."""
        memory_id = _text(memory_id, "memory_id")
        scope_type, scope_id = _text(scope_type, "scope_type"), _text(scope_id, "scope_id")
        validate_page(1, 0, max_bytes)
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 1): raise ValueError("revision must be positive")
        def read(connection: sqlite3.Connection) -> MemoryRecord:
            where, params = "m.memory_id=? AND m.scope_type=? AND m.scope_id=?", [memory_id, scope_type, scope_id]
            if revision is not None: where += " AND m.revision=?"; params.append(revision)
            rows = bounded_rows(connection, where, params, order="m.revision DESC", limit=1, offset=0, max_bytes=max_bytes)
            if not rows: raise SessionNotFound("memory not found in allowed scope")
            return _memory_from_row(rows[0])
        return await self._database.read(read)  # type: ignore[attr-defined]

    async def set_memory_lifecycle(self, memory_id: str, *, revision: int, lifecycle: MemoryLifecycle, scope_type: str | None = None, scope_id: str | None = None) -> MemoryRecord:
        memory_id = _text(memory_id, "memory_id")
        if not isinstance(lifecycle, MemoryLifecycle): raise TypeError("lifecycle must be a MemoryLifecycle")
        timestamp = encode_datetime(utc_now())
        def write(connection: sqlite3.Connection) -> MemoryRecord:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, revision)).fetchone()
            if row is None: raise SessionNotFound("memory revision not found")
            _check_scope(row, scope_type, scope_id)
            latest = connection.execute("SELECT MAX(revision) FROM memories WHERE memory_id=?", (memory_id,)).fetchone()[0]
            if latest != revision: raise ValueError("memory revision conflict; reload before changing lifecycle")
            _check_forgotten(connection, row["scope_type"], row["scope_id"], row["content"])
            connection.execute("UPDATE memories SET lifecycle=?, updated_at=? WHERE memory_id=? AND revision=?", (lifecycle.value, timestamp, memory_id, revision))
            return _memory_from_row(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (memory_id, revision)).fetchone())
        return await self._database.write(write)  # type: ignore[attr-defined]

    async def delete_memory(self, memory_id: str, *, scope_type: str | None = None, scope_id: str | None = None) -> None:
        memory_id = _text(memory_id, "memory_id")
        def write(connection: sqlite3.Connection) -> None:
            rows = connection.execute("SELECT scope_type, scope_id, content FROM memories WHERE memory_id=?", (memory_id,)).fetchall()
            if not rows: raise SessionNotFound("memory not found")
            _check_scope(rows[0], scope_type, scope_id)
            timestamp = encode_datetime(utc_now())
            for row in rows:
                connection.execute("INSERT OR IGNORE INTO memory_forget VALUES (?,?,?,?)", (row["scope_type"], row["scope_id"], hashlib.sha256(row["content"].encode("utf-8")).hexdigest(), timestamp))
                connection.execute("UPDATE memories SET lifecycle='withdrawn',updated_at=? WHERE scope_type=? AND scope_id=? AND content=? AND memory_id<>?", (timestamp, row["scope_type"], row["scope_id"], row["content"], memory_id))
            connection.execute("INSERT OR IGNORE INTO memory_forget VALUES (?,?,?,?)", (rows[0]["scope_type"], rows[0]["scope_id"], "id:" + hashlib.sha256(("memory-id\0" + memory_id).encode("utf-8")).hexdigest(), timestamp))
            connection.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
        await self._database.write(write)  # type: ignore[attr-defined]


def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(row["memory_id"], int(row["revision"]), row["scope_type"], row["scope_id"], row["kind"], row["content"], decode_metadata(row["source_refs"]), row["origin"], decode_metadata(row["conditions"]), MemoryLifecycle(row["lifecycle"]), row["supersedes"], row["derived_from"], decode_datetime(row["created_at"], "memory"), decode_datetime(row["updated_at"], "memory"))


def _search_scopes(project_id: str, user_scope_id: str | None, allow: bool) -> list[tuple[str, str]]:
    if user_scope_id is not None and not allow: raise PermissionError("user-scope memory requires explicit authorization")
    return [("project", project_id)] + ([] if user_scope_id is None else [("user", _text(user_scope_id, "user_scope_id"))])


def _check_scope(row: sqlite3.Row, scope_type: str | None, scope_id: str | None) -> None:
    if (scope_type is None) != (scope_id is None): raise ValueError("scope_type and scope_id must be supplied together")
    if scope_type is not None and (row["scope_type"], row["scope_id"]) != (_text(scope_type, "scope_type"), _text(scope_id, "scope_id")):
        raise SessionNotFound("memory not found in allowed scope")


def _check_forgotten(connection: sqlite3.Connection, scope_type: str, scope_id: str, content: str, *, domain_id: bool = False) -> None:
    digest = ("id:" if domain_id else "") + hashlib.sha256(content.encode("utf-8")).hexdigest()
    if connection.execute("SELECT 1 FROM memory_forget WHERE scope_type=? AND scope_id=? AND content_sha256=?", (scope_type, scope_id, digest)).fetchone() is not None:
        raise ValueError("memory content was explicitly forgotten")
