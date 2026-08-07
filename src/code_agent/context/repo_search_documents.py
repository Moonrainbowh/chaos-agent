from __future__ import annotations

import re
from dataclasses import replace

from .repo_query import expand_search_terms
from .repo_scan import RepoFileFacts


SearchDocument = tuple[str, str, str, str, str, str]
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def bound_search_facts(
    facts: RepoFileFacts,
    max_body_bytes: int,
) -> RepoFileFacts:
    """Apply a deterministic per-file byte quota before records accumulate."""
    if not isinstance(facts, RepoFileFacts):
        raise TypeError("facts must be RepoFileFacts")
    if isinstance(max_body_bytes, bool) or not isinstance(
        max_body_bytes, int
    ):
        raise TypeError("max_body_bytes must be an integer")
    if max_body_bytes < 0:
        raise ValueError("max_body_bytes must not be negative")
    bounded = _head_tail_utf8(facts.search_text, max_body_bytes)
    return (
        facts
        if bounded == facts.search_text
        else replace(facts, search_text=bounded)
    )


def search_document(facts: RepoFileFacts) -> SearchDocument:
    """Build indexed fields while keeping source bodies out of snapshots."""
    body = facts.search_text.casefold()
    symbol_names = " ".join(symbol.name for symbol in facts.symbols)
    contract_terms = _contract_terms(facts.path, body)
    return (
        facts.path,
        expand_search_terms(facts.path),
        expand_search_terms(symbol_names),
        _auxiliary_terms(body),
        contract_terms,
        body,
    )


def _head_tail_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    if limit == 0:
        return ""
    if limit < 4:
        return encoded[:limit].decode("utf-8", errors="ignore")
    head_size = (limit - 1) * 3 // 4
    tail_size = limit - 1 - head_size
    head = encoded[:head_size].decode("utf-8", errors="ignore")
    tail = encoded[-tail_size:].decode("utf-8", errors="ignore")
    return f"{head}\n{tail}"


def _auxiliary_terms(text: str) -> str:
    terms: list[str] = []
    for word in _ASCII_WORD.findall(text):
        if len(word) <= 2:
            terms.append(word)
    for run in _CJK_RUN.findall(text):
        terms.extend(run)
        terms.extend(
            run[offset : offset + 2]
            for offset in range(len(run) - 1)
        )
    return " ".join(dict.fromkeys(terms))


def _contract_terms(path: str, body: str) -> str:
    if path.replace("\\", "/").rsplit("/", 1)[-1].casefold() != "agents.md":
        return ""
    positive: list[str] = []
    before_sections = True
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            before_sections = False
        if (
            before_sections
            and stripped
            and not stripped.startswith("#")
            and not stripped.startswith("-")
        ):
            positive.append(stripped)
        elif stripped.startswith("# "):
            positive.append(stripped[2:])
        elif (
            stripped.startswith("-")
            and not stripped.startswith("- 不负责")
        ):
            positive.append(stripped)
    text = " ".join(positive)
    return f"{text} {_auxiliary_terms(text)}".strip()
