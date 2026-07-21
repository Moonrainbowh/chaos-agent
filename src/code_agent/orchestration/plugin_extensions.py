from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from code_agent.plugins.models import AgentContribution, ModeContribution
from code_agent.plugins.registry import PluginHost

from .models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    ModeSnapshot,
    ReasoningEffort,
)


@dataclass(frozen=True)
class PluginModeSnapshot:
    identifier: str
    plugin_id: str
    digest: str
    generation: int
    base: ModeSnapshot
    prompt_policy: str
    tool_names: tuple[str, ...]
    reasoning_effort: ReasoningEffort


@dataclass(frozen=True)
class DelegationSelector:
    role: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if (self.role is None) == (self.agent_id is None):
            raise ValueError("exactly one of role or agent_id is required")
        if self.role is not None and (
            not isinstance(self.role, str) or not self.role.strip()
        ):
            raise ValueError("role must be non-blank text")
        if self.agent_id is not None and (
            not isinstance(self.agent_id, str) or "." not in self.agent_id
        ):
            raise ValueError("agent_id must be namespaced")


class PluginModeCatalog:
    def __init__(
        self, host: PluginHost, bases: Mapping[AgentMode, ModeSnapshot]
    ) -> None:
        self._host = host
        self._bases = dict(bases)

    def resolve(self, identifier: str) -> PluginModeSnapshot:
        registered = _registered(self._host, "mode", identifier)
        value = registered.value
        if not isinstance(value, ModeContribution):
            raise TypeError("invalid plugin mode contribution")
        base = self._bases[value.base_mode]
        return PluginModeSnapshot(
            registered.qualified_id,
            registered.plugin_id,
            self._host.manifest_digest(registered.plugin_id),
            self._host.generation,
            base,
            value.prompt_policy,
            value.tool_names,
            value.reasoning_effort or base.definition.reasoning_effort,
        )


class PluginAgentCatalog:
    def __init__(
        self, host: PluginHost, bases: Mapping[AgentMode, ModeSnapshot]
    ) -> None:
        self._host = host
        self._bases = dict(bases)

    def resolve(self, identifier: str) -> AgentDefinition:
        registered = _registered(self._host, "agent", identifier)
        value = registered.value
        if not isinstance(value, AgentContribution):
            raise TypeError("invalid plugin agent contribution")
        base = self._bases[value.base_mode]
        return AgentDefinition(
            registered.qualified_id,
            AgentRole.CUSTOM,
            base,
            value.instructions,
            value.tool_names,
            value.may_write,
            0,
        )


def _registered(host: PluginHost, kind: str, identifier: str) -> object:
    if not isinstance(identifier, str) or "." not in identifier:
        raise ValueError("plugin contribution id must be namespaced")
    result = next(
        (
            item
            for item in host.contributions(kind)
            if item.qualified_id == identifier
        ),
        None,
    )
    if result is None:
        raise KeyError("plugin contribution is unavailable")
    return result
