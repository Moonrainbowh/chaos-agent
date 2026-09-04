from __future__ import annotations

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
