"""Scoped lexical retrieval; SQL filters and pages before record decoding."""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from code_agent.core._json import plain


def condition_status(conditions: Mapping, context: Mapping) -> str:
    encode = lambda value: json.dumps(plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if any(key in context and encode(context[key]) != encode(expected) for key, expected in conditions.items()):
        return "conflict"
    if not conditions or any(key not in context for key in conditions):
        return "needs_check"
    return "applicable"


def query_terms(query: str) -> tuple[str, ...]:
    terms = []
    for token in re.findall(r"[\u3400-\u9fff]+|[^\W_]+", query.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 1:
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return tuple(dict.fromkeys(terms))[:64]


def validate_page(limit: int, offset: int, max_bytes: int, ceiling: int = 100) -> None:
    for value, name, lower, upper in ((limit, "limit", 1, ceiling), (offset, "offset", 0, 2**31 - 1), (max_bytes, "max_bytes", 1, 16 * 1024 * 1024)):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f"{name} must be {lower}..{upper}")


def row_bytes_sql(alias: str = "m") -> str:
    return "+".join(f"length(CAST({alias}.{name} AS BLOB))" for name in ("memory_id", "scope_type", "scope_id", "kind", "content", "source_refs", "origin", "conditions"))


def bounded_rows(connection: sqlite3.Connection, where: str, params: Sequence, *, order: str, limit: int, offset: int, max_bytes: int) -> tuple[sqlite3.Row, ...]:
    identities = connection.execute(f"SELECT m.memory_id,m.revision,{row_bytes_sql()} AS bytes FROM memories m WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    if sum(row["bytes"] for row in identities) > max_bytes:
        raise ValueError("memory page exceeds max_bytes; lower limit or increase explicit byte allowance")
    return tuple(connection.execute("SELECT * FROM memories WHERE memory_id=? AND revision=?", (row["memory_id"], row["revision"])).fetchone() for row in identities)


def search_plan(connection: sqlite3.Connection, scopes: Sequence[tuple[str, str]], query: str, context: Mapping | None, applicability: Sequence[str] | None, max_bytes: int, require_applicable: bool = False) -> tuple[str, list, str, str, tuple[str, ...]]:
    terms = query_terms(query)
    connection.create_function("memory_score", 2, lambda content, kind: sum(term in (content + " " + kind).casefold() for term in terms))
    def status(encoded: str) -> str:
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValueError("memory conditions exceed 128 KiB")
        conditions = json.loads(encoded)
        if not isinstance(conditions, dict):
            raise ValueError("memory conditions must be an object")
        if require_applicable and not conditions:
            return "applicable"
        return condition_status(conditions, context or {})
    connection.create_function("memory_applicability", 1, status)
    scope_where = "(" + " OR ".join("(m.scope_type=? AND m.scope_id=?)" for _ in scopes) + ")"
    params = [value for scope in scopes for value in scope]
    latest = "m.revision=(SELECT MAX(n.revision) FROM memories n WHERE n.memory_id=m.memory_id)"
    base = f"{scope_where} AND {latest}"
    eligible = base + " AND m.lifecycle='active'"
    if require_applicable:
        if applicability is not None:
            raise ValueError("require_applicable cannot be combined with applicability")
        applicability = ("applicable",)
    if applicability is not None:
        if context is None:
            raise ValueError("applicability filtering requires context")
        if not applicability or any(value not in {"applicable", "needs_check", "conflict"} for value in applicability):
            raise ValueError("invalid applicability statuses")
        eligible += " AND CASE WHEN length(CAST(m.conditions AS BLOB))<=131072 THEN memory_applicability(m.conditions) IN (" + ",".join("?" for _ in applicability) + ") ELSE 0 END"
        params.extend(applicability)
    # Byte filtering precedes invoking a lexical function on body text.
    matched = eligible + f" AND CASE WHEN {row_bytes_sql()}<=? THEN memory_score(m.content,m.kind)>0 ELSE 0 END"
    params.append(max_bytes)
    return matched, params, base, eligible, terms
