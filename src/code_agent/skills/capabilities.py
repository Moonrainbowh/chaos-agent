from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from code_agent.core.models import ToolDefinition

from .registry import SkillManifest


@dataclass(frozen=True)
class CapabilityCheck:
    """A read-only comparison between Skill requirements and exposed tools."""

    skill_id: str
    required: tuple[str, ...]
    available: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return not self.missing


class SkillCapabilityError(PermissionError):
    """Raised when a Skill cannot run with the current tool snapshot."""

    def __init__(self, checks: tuple[CapabilityCheck, ...]) -> None:
        self.checks = checks
        missing = ", ".join(
            f"{check.skill_id}: {', '.join(check.missing)}"
            for check in checks
            if check.missing
        )
        super().__init__(f"Skill capabilities unavailable ({missing})")


def check_capabilities(
    skill: SkillManifest,
    tools: Iterable[ToolDefinition],
    *,
    aliases: Mapping[str, str] | None = None,
) -> CapabilityCheck:
    """Report capability availability without changing disclosure or authorization."""
    snapshot = tuple(tools)
    if not all(isinstance(tool, ToolDefinition) for tool in snapshot):
        raise TypeError("capability checks require ToolDefinition snapshots")
    names = {tool.name for tool in snapshot}
    aliases = aliases or {}
    required = tuple(dict.fromkeys(skill.requires))
    available = tuple(item for item in required if aliases.get(item, item) in names)
    missing = tuple(item for item in required if item not in available)
    return CapabilityCheck(skill.identifier, required, available, missing)
