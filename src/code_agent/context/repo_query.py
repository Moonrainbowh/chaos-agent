from __future__ import annotations

import re
from dataclasses import dataclass


_ASCII_TERM = re.compile(r"[A-Za-z0-9_./-]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR = re.compile(r"[^A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]+")
_MAX_QUERY_CHARS = 512
_MAX_TERMS = 16
_MAX_SHORTS = 8
_MAX_TERM_CHARS = 64
_MAX_COLLECTED_TERMS = _MAX_QUERY_CHARS * 2
_CONTRACT_STOP_TERMS = frozenset(
    {
        "哪里",
        "在哪",
        "怎么",
        "如何",
        "实现",
        "代码",
        "项目",
        "这个",
        "帮我",
        "请帮",
        "负责",
        "究竟",
        "分析",
    }
)
_QUERY_ALIASES = (
    ("权限", ("policy", "permission", "authorization", "approval")),
    ("审批", ("approval", "authorize", "policy")),
    ("校验", ("validate", "validation", "verify", "verification")),
    ("验证", ("verify", "verification", "validate")),
    ("回溯", ("rewind", "rollback", "restore", "checkpoint")),
    ("恢复", ("restore", "recovery", "resume")),
    ("会话", ("session", "thread")),
    ("附件", ("attachment",)),
    ("检索", ("search", "retrieval", "index")),
    ("搜索", ("search", "retrieval")),
    ("索引", ("index",)),
    ("上下文", ("context",)),
    ("预算", ("budget",)),
    ("命令", ("command",)),
    ("工作区", ("workspace",)),
)


@dataclass(frozen=True)
class RepoQueryPlan:
    terms: tuple[str, ...]
    trigrams: tuple[str, ...]
    shorts: tuple[str, ...]


def plan_repo_query(query: str) -> RepoQueryPlan:
    """Convert untrusted text into bounded literal FTS terms."""
    bounded = bound_repo_query(query)
    terms: list[str] = []
    grams: list[str] = []
    shorts: list[str] = []
    for raw in _ASCII_TERM.findall(bounded):
        for term in (raw, *expand_search_terms(raw).split()):
            _append_unique(terms, term.casefold())
        if len(raw) >= 3:
            _append_unique(grams, raw.casefold())
        else:
            _append_unique(shorts, raw.casefold())
    for run in _CJK_RUN.findall(bounded):
        _append_unique(terms, run)
        if len(run) < 3:
            _append_unique(shorts, run)
            continue
        for offset in range(len(run) - 1):
            _append_unique(shorts, run[offset : offset + 2])
        for offset in range(len(run) - 2):
            _append_unique(grams, run[offset : offset + 3])
    for source, aliases in _QUERY_ALIASES:
        if source in bounded:
            for alias in aliases:
                _append_unique(terms, alias)
    selected_grams = _select_terms(grams, _MAX_TERMS)
    return RepoQueryPlan(
        _select_terms(terms, _MAX_TERMS),
        selected_grams,
        _select_shorts(shorts, selected_grams),
    )


def bound_repo_query(query: str) -> str:
    """Return the exact raw prefix considered by every retrieval channel."""
    if not isinstance(query, str):
        raise TypeError("query must be text")
    return query[:_MAX_QUERY_CHARS]


def expand_search_terms(text: str) -> str:
    """Expose path, snake-case, and camel-case pieces to unicode61."""
    separated = _SEPARATOR.sub(" ", _CAMEL_BOUNDARY.sub(" ", text))
    return f"{text} {separated}".casefold()


def contract_query_terms(plan: RepoQueryPlan) -> tuple[str, ...]:
    """Prefer domain literals over interrogative wording for contracts."""
    if not isinstance(plan, RepoQueryPlan):
        raise TypeError("plan must be a RepoQueryPlan")
    candidates = (*plan.shorts, *plan.terms)
    return tuple(
        term
        for term in dict.fromkeys(candidates)
        if term not in _CONTRACT_STOP_TERMS
    )[:_MAX_TERMS]


def quote_fts_term(term: str) -> str:
    """Encode one literal term without exposing FTS query operators."""
    return '"' + term.replace('"', '""') + '"'


def _append_unique(target: list[str], value: str) -> None:
    bounded = value[:_MAX_TERM_CHARS].strip()
    if (
        bounded
        and bounded not in target
        and len(target) < _MAX_COLLECTED_TERMS
    ):
        target.append(bounded)


def _select_terms(values: list[str], limit: int) -> tuple[str, ...]:
    if len(values) <= limit:
        return tuple(values)
    head_count = max(1, limit // 4)
    tail_count = max(1, limit // 2)
    middle_count = limit - head_count - tail_count
    selected = [*values[:head_count]]
    middle = values[head_count : len(values) - tail_count]
    if middle_count and middle:
        step = len(middle) / middle_count
        selected.extend(
            middle[min(int(offset * step), len(middle) - 1)]
            for offset in range(middle_count)
        )
    selected.extend(values[-tail_count:])
    return tuple(dict.fromkeys(selected))


def _select_shorts(
    values: list[str],
    selected_trigrams: tuple[str, ...],
) -> tuple[str, ...]:
    prioritized: list[str] = []
    available = set(values)
    for gram in reversed(selected_trigrams):
        for pair in (gram[1:], gram[:2]):
            if pair in available and pair not in prioritized:
                prioritized.append(pair)
                if len(prioritized) == _MAX_SHORTS:
                    return tuple(prioritized)
    for value in _select_terms(values, _MAX_SHORTS):
        if value not in prioritized:
            prioritized.append(value)
            if len(prioritized) == _MAX_SHORTS:
                break
    return tuple(prioritized)
