"""Pure reconstruction of bounded, paired Host progress facts."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from collections.abc import Mapping

from ._json import plain
from .action_semantics import READ_ONLY_TOOLS, resolve_supervision_call
from .models import ActionResult, Message, ToolCall
from code_agent.capabilities.catalog import disclosed_contract

_VOLATILE = frozenset({"request_id", "call_id", "duration", "duration_ms", "elapsed_ms"})
_BROAD = re.compile(r"全仓库|整个仓库|全项目|整个项目|repository[ -]wide|whole repository|entire repository", re.I)
_SCOPE_CLAUSE = re.compile(r"[;；，,。!?！？\n]|\.(?=\s|$)|\b(?:but|however)\b|但是", re.I)
_SCOPE_NEGATION = re.compile(r"\b(?:not|never|avoid)\b|n['’]t\b|不要|无需|不用|不必|不需要|不做|不进行|请勿|不得|不允许|禁止|不应|别|不(?:检查|查看|读取|调查|审查|扫描)", re.I)
_PATH_DENIAL = re.compile(
    r"(?:\bnot|don't|don’t|never|avoid)\s+(?:inspect|read|scan|investigate|search|examine|access|load|browse|look at)\b"
    r"|(?:不要|无需|请勿|不得|不允许|别|不用|不必|禁止|不应)\s*(?:检查|查看|读取|调查|扫描|搜索|访问|审查|浏览)"
    r"|\b(?:except|excluding)\b|排除", re.I)
_PATH = re.compile(r"(?<![\w])(?:[\w.-]+[/\\])+[\w./\\-]*|[\w.-]+\.[A-Za-z0-9]{1,12}")
_SYMBOL = re.compile(r"`([^`\n]+)`|\b([A-Za-z][A-Za-z0-9]*[_:][A-Za-z0-9_:]+)\b")
_QUERY = re.compile(r"(?:搜索|查找|search(?: for)?|find)\s+[\"']?([A-Za-z_][A-Za-z0-9_]*)", re.I)
_HISTORY_CANDIDATES = frozenset({"history_list_windows", "history_list_items", "history_read_item",
    "history_search_contents", "notes_list_files", "notes_read_file", "notes_search_contents"})


def stable_value(value: object) -> object:
    """Remove transport timing/identity recursively, retaining actual content."""
    value = plain(value)
    if isinstance(value, Mapping):
        return {str(k): stable_value(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, (tuple, list)):
        return [stable_value(v) for v in value]
    return value


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(stable_value(value), sort_keys=True,
        ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def interaction_revision(messages: tuple[Message, ...]) -> int:
    """A duplicate resume input is not a new user requirement."""
    return len({fingerprint({"content": message.content,
        "attachments": [item.to_dict() for item in message.attachments]})
        for message in messages if message.role == "user"})


def explicit_positive_clauses(objective: str) -> tuple[str, ...]:
    """Return only explicitly affirmative clauses for scope/lease hints.

    This finite negative vocabulary is a conservative hint filter, not language
    understanding or an authorization decision. File extension dots stay intact.
    """
    return tuple(clause for clause in _SCOPE_CLAUSE.split(objective)
                 if clause.strip() and not _SCOPE_NEGATION.search(clause))


def _explicit_broad_scope(objective: str) -> bool:
    """A negated whole-repository mention is not a positive scope seed.

    Only explicit phrases are considered. Conflicting broad instructions stay
    narrow; separate unrelated negations do not cancel a positive declaration.
    """
    clauses = [clause for clause in _SCOPE_CLAUSE.split(objective) if _BROAD.search(clause)]
    positive = set(explicit_positive_clauses(objective))
    return bool(clauses) and all(clause in positive for clause in clauses)


def _path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    value = posixpath.normpath(value.replace("\\", "/")).removeprefix("./")
    if value == ".." or value.startswith("../") or value.startswith("/") or ":" in value:
        return ""
    return value.rstrip("/")


def _within(path: str, anchors: set[str]) -> bool:
    return bool(path) and any(path == anchor or path.startswith(anchor + "/") for anchor in anchors)


def _paths(output: Mapping) -> tuple[str, ...]:
    found = []
    for key in ("matches", "slices", "files", "entries"):
        items = output.get(key)
        if not isinstance(items, (tuple, list)):
            continue
        for item in items:
            path = _path(item.get("path") if isinstance(item, Mapping) else item)
            if path:
                found.append(path)
    return tuple(found)


def _meaningful(output: Mapping) -> bool:
    return any(bool(output.get(key)) for key in
        ("content", "text", "matches", "slices", "files", "entries", "items", "diff", "changes"))


@dataclass(frozen=True)
class HostProgress:
    candidate_digest: str
    renewal_digest: str
    candidate_count: int
    related_count: int
    resolved_count: int


def observe_host_progress(messages: tuple[Message, ...], objective: str,
                         dispatcher: object, *, candidate_limit: int,
                         hard_tool_limit: int, projection: dict | None = None) -> HostProgress:
    """Replay trusted paired outcomes; candidate reads alone cannot renew.

    The accepted sets never evict and re-accept facts. Saturation is conservative.
    Explicit task paths and symbol searches seed a one-hop Host result path chain;
    arbitrary model search queries and metadata cannot introduce relevance.
    """
    clauses = _SCOPE_CLAUSE.split(objective)
    anchors = {_path(value) for clause in clauses if not _PATH_DENIAL.search(clause)
               for value in _PATH.findall(clause)} - {""}
    excluded = {_path(value) for clause in clauses if _PATH_DENIAL.search(clause)
                for value in _PATH.findall(clause)} - {""}
    read_clauses = [clause for clause in clauses if not _PATH_DENIAL.search(clause)]
    symbols = {a or b for clause in read_clauses for a, b in _SYMBOL.findall(clause)}
    symbols.update(value for clause in read_clauses for value in _QUERY.findall(clause))
    broad = _explicit_broad_scope(objective)
    stored = projection or {}
    if stored and stored.get("identity") != fingerprint((objective,candidate_limit,hard_tool_limit)):
        raise ValueError("Host progress frozen identity changed")
    anchors.update(stored.get("anchors", ()))
    candidates: set[str] = set(stored.get("candidates", ()))
    related: set[str] = set(stored.get("related", ()))
    resolved: set[str] = set(stored.get("resolved", ()))
    failures: set[str] = set(stored.get("failures", ()))
    calls: dict[str, ToolCall] = {value["id"]: ToolCall.from_dict(value) for value in stored.get("calls", ())}
    seen_ids: set[str] = set(stored.get("seen_ids", ()))
    user_revisions = set(stored.get("user_revisions", ()))
    for message in messages:
        if message.role == "user":
            user_revisions.add(fingerprint({"content": message.content,
                "attachments": [item.to_dict() for item in message.attachments]}))
            if projection is not None and len(user_revisions)>4096:
                raise ValueError("user revision projection exceeds bounded capacity")
        if message.role == "assistant":
            for call in message.tool_calls:
                if call.id not in seen_ids:
                    calls[call.id] = call
                    # Production rejects historic duplicate IDs against Sessions;
                    # the reducer retains only a bounded local replay guard.
                    if projection is None or len(seen_ids)<hard_tool_limit:
                        seen_ids.add(call.id)
                    if projection is not None and len(calls)>1000:
                        raise ValueError("open progress tool group exceeds bounded capacity")
            continue
        if message.role != "tool":
            continue
        original = calls.pop(message.tool_call_id, None)
        if original is None or original.name != message.name:
            continue
        call = resolve_supervision_call(original, dispatcher)
        try:
            result = ActionResult.from_dict(json.loads(message.content))
        except (ValueError, KeyError, TypeError):
            continue
        if result.request_id != original.id or result.name != original.name:
            continue
        signature = fingerprint({"name": call.name, "arguments": call.arguments})
        if result.is_error:
            if len(failures) < hard_tool_limit and (call.name in READ_ONLY_TOOLS or (
                    call.name in {"run_command", "run_process_v1"}
                    and result.metadata.get("execution_attempted") is True)):
                failures.add(signature)
            continue
        if call.name in {"run_command", "run_process_v1"}:
            if (signature in failures and result.metadata.get("execution_attempted") is True
                    and isinstance(result.output, Mapping) and result.output.get("returncode") == 0
                    and not isinstance(result.output.get("returncode"), bool)
                    and len(resolved) < hard_tool_limit):
                resolved.add(signature)
            continue
        if call.name == "load_tool_contract" or call.name in _HISTORY_CANDIDATES:
            disclosure = disclosed_contract(result) if call.name == "load_tool_contract" else None
            if disclosure is not None:
                candidate = fingerprint({"contract": disclosure})
            elif call.name in _HISTORY_CANDIDATES and isinstance(result.output, Mapping) and _meaningful(result.output):
                output = {k: v for k, v in result.output.items() if k != "revision"}
                candidate = fingerprint({"name": call.name, "output": output})
            else:
                continue
            if len(candidates) < candidate_limit:
                candidates.add(candidate)
            continue  # Persisted reads/disclosure are bounded hints, never renewal/proof.
        if call.name not in READ_ONLY_TOOLS or not isinstance(result.output, Mapping):
            continue
        output = result.output
        if not _meaningful(output):
            continue
        target = _path(call.arguments.get("path") or call.arguments.get("root"))
        result_paths = _paths(output)
        query = call.arguments.get("query") or call.arguments.get("pattern")
        symbol_query = isinstance(query, str) and query.strip() in symbols
        relevant = broad or _within(target, anchors)
        if call.name == "search_text" and symbol_query:
            relevant = bool(result_paths)
        if _within(target, excluded) or any(_within(path, excluded) for path in result_paths):
            relevant = False
        if call.name == "read_code_slices":
            relevant = bool(result_paths) and all(
                (broad or _within(path, anchors)) and not _within(path, excluded) for path in result_paths)
        if call.name == "search_text" and relevant:
            for path in result_paths:
                if len(anchors) >= hard_tool_limit:
                    break
                if not _within(path, excluded) and (broad or symbol_query or _within(path, anchors)):
                    anchors.add(path)
        identity = {"name": call.name, "resource": target if call.name == "read_file" else "",
                    "output": output}
        digest = fingerprint(identity)
        if len(candidates) < candidate_limit:
            candidates.add(digest)
        if relevant and len(related) < hard_tool_limit:
            related.add(digest)
            if signature in failures and len(resolved) < hard_tool_limit:
                resolved.add(signature)
    if projection is not None:
        projection.clear()
        projection.update(identity=fingerprint((objective,candidate_limit,hard_tool_limit)),
            anchors=sorted(anchors),candidates=sorted(candidates),related=sorted(related),
            resolved=sorted(resolved),failures=sorted(failures),seen_ids=sorted(seen_ids),
            calls=[value.to_dict() for value in calls.values()],user_revisions=sorted(user_revisions))
    return HostProgress(fingerprint(sorted(candidates)),
        fingerprint({"related": sorted(related), "resolved": sorted(resolved)}),
        len(candidates), len(related), len(resolved))
