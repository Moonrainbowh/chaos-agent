from __future__ import annotations

import re

from enum import Enum
from pathlib import PurePosixPath
from typing import Sequence


class RiskTier(str, Enum):
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

_TIER_ORDER = (RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL)
_MAX_PATCH_CHARS = 262144
_SYMBOL = re.compile(r"(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_HIGH_RULES = (
    (re.compile(r"(?<!&)&(?!&)|(?<!\|)\|(?!\|)|\^|<<|>>|~"), "Bitwise operation changed"),
    (re.compile(r"\b(?:async\s+)?def\s+\w+\s*\("), "Function signature added or removed"),
    (re.compile(r"(?:parse|parser|decode|encode|protocol|state_machine|transition)", re.I), "Parser, protocol, or state-transition logic changed"),
)
_CONTROL_FLOW = re.compile(r"\b(?:if|elif|else|except|raise|try|finally|for|while|match|case)\b")


def classify_patch_risk(
    changed_files: Sequence[str], diff: str = "",
) -> tuple[RiskTier, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Classify both patch sides; unknown or omitted bodies retain path risk.

    Rules are conservative cues, not proof of behavior or branch coverage.
    High-risk plans request existing related tests and the final project gate.
    """
    tier, path_reason = classify_risk(changed_files)
    reasons = [path_reason]
    if not diff or tier is RiskTier.LOW:
        return tier, (), tuple(reasons), ()
    truncated = len(diff) > _MAX_PATCH_CHARS or "PATCH_BODY_TRUNCATED" in diff
    patch = diff[:_MAX_PATCH_CHARS]
    changed = [line[1:].strip() for line in patch.splitlines()
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    code = [line for line in changed if line and not line.startswith(("#", "//"))]
    symbol_lines = code + [line for line in patch.splitlines() if line.startswith("@@")]
    symbols = tuple(sorted({match.group(1) for line in symbol_lines
                            for match in _SYMBOL.finditer(line)}))
    if truncated:
        reasons.append("Patch body incomplete; require conservative verification")
        tier = max(tier, RiskTier.HIGH, key=_TIER_ORDER.index)
    elif changed and not code and tier is RiskTier.MEDIUM:
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
    checks = ("related_behavior_tests", "final_project_gate") if tier in {
        RiskTier.HIGH, RiskTier.CRITICAL
    } else ()
    return tier, symbols, tuple(reasons), checks


__all__ = ["RiskTier", "classify_risk", "classify_patch_risk"]
