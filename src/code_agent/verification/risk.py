from __future__ import annotations

import re

from enum import Enum
from pathlib import PurePosixPath
from typing import Sequence


class RiskTier(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_LOW_RISK_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".rst", ".adoc",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    }
)
_LOW_RISK_FILENAMES = frozenset(
    {
        "license", "license.md", "license.txt",
        ".gitignore", ".gitattributes", ".editorconfig",
        "readme", "readme.md",
    }
)
_DOCUMENT_NAMES = frozenset(
    {
        "readme", "readme.md", "license", "license.md", "license.txt",
        "changelog.md", "contributing.md",
    }
)
_CRITICAL_PATTERNS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-",
    "alembic",
    "migrations",
    "_database.py",
    "_schema_",
)
_HIGH_RISK_SUBSYSTEMS = (
    "core/",
    "runtime/",
    "policy/",
    "workspace/",
    "sessions/",
    "checkpoints/",
)


def classify_risk(changed_files: Sequence[str]) -> tuple[RiskTier, str]:
    """Classify a change conservatively from paths before graph escalation."""
    if not changed_files:
        return RiskTier.LOW, "No changed files detected"
    normalized = [path.replace("\\", "/") for path in changed_files]
    for path in normalized:
        lowered = path.casefold()
        if any(pattern in lowered for pattern in _CRITICAL_PATTERNS):
            return (
                RiskTier.CRITICAL,
                f"Critical configuration or schema modified: {path}",
            )
    for path in normalized:
        lowered = path.casefold()
        if any(subsystem in lowered for subsystem in _HIGH_RISK_SUBSYSTEMS):
            return RiskTier.HIGH, f"Core subsystem modified: {path}"
    if all(
        PurePosixPath(path).suffix.casefold() in _LOW_RISK_EXTENSIONS
        or PurePosixPath(path).name.casefold() in _LOW_RISK_FILENAMES
        for path in normalized
    ):
        return (
            RiskTier.LOW,
            "All changed files are documentation or non-executable assets",
        )
    return RiskTier.MEDIUM, "Changes affect local feature logic or modules"

_TIER_ORDER = (
    RiskTier.TRIVIAL,
    RiskTier.LOW,
    RiskTier.MEDIUM,
    RiskTier.HIGH,
    RiskTier.CRITICAL,
)
_MAX_PATCH_CHARS = 262144
_TRIVIAL_MAX_CHANGED_LINES = 20
_SYMBOL = re.compile(r"(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_HIGH_RULES = (
    (re.compile(r"(?<!&)&(?!&)|(?<!\|)\|(?!\|)|\^|<<|>>|~"), "Bitwise operation changed"),
    (re.compile(r"\b(?:async\s+)?def\s+\w+\s*\("), "Function signature added or removed"),
    (re.compile(r"(?:parse|parser|decode|encode|protocol|state_machine|transition)", re.I), "Parser, protocol, or state-transition logic changed"),
)
_CONTROL_FLOW = re.compile(r"\b(?:if|elif|else|except|raise|try|finally|for|while|match|case)\b")
_TRIVIAL_BLOCKERS = (
    re.compile(r"\b(?:import|export|from\s+\S+\s+import|__all__)\b"),
    re.compile(r"\bclass\s+\w+\b"),
    re.compile(r"\b(?:auth|permission|credential|secret|token|encrypt|decrypt|sandbox)\w*\b", re.I),
    re.compile(r"\b(?:database|migration|schema|serializ|persist|checkpoint|recovery|restore)\w*\b", re.I),
    re.compile(r"\b(?:async|await|thread|process|concurren|lock|mutex|semaphore)\w*\b", re.I),
    re.compile(r"\b(?:network|socket|http|https|request|response|protocol)\w*\b", re.I),
    re.compile(r"(?:<script\b|<form\b|\bon[a-z]+\s*=|\baction\s*=)", re.I),
)
_TRIVIAL_EXCLUDED_SUFFIXES = frozenset(
    {".cfg", ".env", ".ini", ".json", ".toml", ".yaml", ".yml"}
)
_TRIVIAL_ENTRY_NAMES = frozenset(
    {"__main__.py", "app.py", "cli.py", "main.py", "agent_app.py", "agent_cli.py"}
)
_TRIVIAL_EXCLUDED_PARTS = frozenset(
    {".github", "ci", "fixture", "fixtures", "generated", "snapshot", "snapshots", "tests", "vendor", "vendored"}
)


def _trivial_path_allowed(raw: str) -> bool:
    path = PurePosixPath(raw.replace("\\", "/"))
    name = path.name.casefold()
    parts = {part.casefold() for part in path.parts[:-1]}
    return not (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name in _TRIVIAL_ENTRY_NAMES
        or path.suffix.casefold() in _TRIVIAL_EXCLUDED_SUFFIXES
        or bool(parts.intersection(_TRIVIAL_EXCLUDED_PARTS))
    )


def _clear_documentation_paths(changed_files: Sequence[str]) -> bool:
    for raw in changed_files:
        path = PurePosixPath(raw.replace("\\", "/"))
        parts = {part.casefold() for part in path.parts[:-1]}
        if path.name.casefold() in _DOCUMENT_NAMES:
            continue
        if parts.intersection({"docs", "doc", "documentation"}):
            continue
        return False
    return bool(changed_files)


def _changed_patch_lines(patch: str) -> list[str]:
    return [
        line[1:]
        for line in patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def _is_trivial_candidate(
    tier: RiskTier,
    changed_files: Sequence[str],
    changed_lines: Sequence[str],
    text: str,
) -> bool:
    return (
        tier in {RiskTier.LOW, RiskTier.MEDIUM}
        and len(changed_files) == 1
        and 0 < len(changed_lines) <= _TRIVIAL_MAX_CHANGED_LINES
        and _trivial_path_allowed(changed_files[0])
        and not _CONTROL_FLOW.search(text)
        and not any(rule.search(text) for rule in _TRIVIAL_BLOCKERS)
    )


def _trivial_reason(changed_line_count: int, *, signals_checked: bool) -> str:
    reason = (
        f"Single-file patch has {changed_line_count} changed lines "
        f"within the {_TRIVIAL_MAX_CHANGED_LINES}-line trivial limit"
    )
    if signals_checked:
        reason += " and no forced-escalation signal"
    return reason


def classify_patch_risk(
    changed_files: Sequence[str], diff: str = "",
) -> tuple[RiskTier, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Classify both patch sides; unknown or omitted bodies retain path risk.

    Rules are conservative cues, not proof of behavior or branch coverage.
    High-risk plans request existing related tests and the final project gate.
    """
    tier, path_reason = classify_risk(changed_files)
    reasons = [path_reason]
    if not diff or (tier is RiskTier.LOW and _clear_documentation_paths(changed_files)):
        return tier, (), tuple(reasons), ()
    truncated = (
        len(diff) > _MAX_PATCH_CHARS
        or "PATCH_BODY_TRUNCATED" in diff
        or "GIT binary patch" in diff
        or "Binary files " in diff
    )
    patch = diff[:_MAX_PATCH_CHARS]
    changed_lines = _changed_patch_lines(patch)
    changed = [line.strip() for line in changed_lines]
    code = [line for line in changed if line and not line.startswith(("#", "//"))]
    symbol_lines = code + [line for line in patch.splitlines() if line.startswith("@@")]
    symbols = tuple(sorted({match.group(1) for line in symbol_lines
                            for match in _SYMBOL.finditer(line)}))
    if truncated:
        reasons.append("Patch body incomplete; require conservative verification")
        tier = max(tier, RiskTier.HIGH, key=_TIER_ORDER.index)
    elif changed and not code:
        if _is_trivial_candidate(tier, changed_files, changed_lines, ""):
            tier = RiskTier.TRIVIAL
            reasons = [_trivial_reason(len(changed_lines), signals_checked=False)]
        elif tier is RiskTier.MEDIUM:
            tier = RiskTier.LOW
            reasons = ["Patch changes only comments or whitespace"]
    else:
        text = "\n".join(code)
        for rule, reason in _HIGH_RULES:
            if rule.search(text):
                tier = max(tier, RiskTier.HIGH, key=_TIER_ORDER.index)
                reasons.append(reason)
        if _CONTROL_FLOW.search(text):
            reasons.append("Control-flow or exception handling changed")
        if _is_trivial_candidate(tier, changed_files, changed_lines, text):
            tier = RiskTier.TRIVIAL
            reasons = [_trivial_reason(len(changed_lines), signals_checked=True)]
    checks = ("related_behavior_tests", "final_project_gate") if tier in {
        RiskTier.HIGH, RiskTier.CRITICAL
    } else ()
    return tier, symbols, tuple(reasons), checks


__all__ = ["RiskTier", "classify_risk", "classify_patch_risk"]
