from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from code_agent.providers.config import ModelProfile
from code_agent.core.limits import EngineLimits

from .models import (
    AgentMode,
    ModeDefinition,
    ModeSnapshot,
    ReasoningEffort,
)
from .codec import limits_payload, snapshot_payload


class ModeRegistry:
    def __init__(self, definitions: Sequence[ModeDefinition]) -> None:
        copied = tuple(definitions)
        if not copied:
            raise ValueError("at least one mode definition is required")
        by_mode = {definition.mode: definition for definition in copied}
        if len(by_mode) != len(copied):
            raise ValueError("mode definitions must be unique")
        missing = set(AgentMode) - set(by_mode)
        if missing:
            names = ", ".join(sorted(mode.value for mode in missing))
            raise ValueError(f"standard mode definitions are missing: {names}")
        self._definitions = by_mode

    def definitions(self) -> tuple[ModeDefinition, ...]:
        return tuple(self._definitions[mode] for mode in AgentMode)

    def definition(self, mode: AgentMode | str) -> ModeDefinition:
        selected = AgentMode(mode)
        return self._definitions[selected]

    def freeze(
        self,
        mode: AgentMode | str,
        profiles: Mapping[str, ModelProfile],
    ) -> ModeSnapshot:
        definition = self.definition(mode)
        try:
            profile = profiles[definition.profile_id]
        except KeyError:
            raise ValueError(f"mode profile is not configured: {definition.profile_id}") from None
        oracle_model = None
        if definition.oracle_profile_id is not None:
            try:
                oracle_model = profiles[definition.oracle_profile_id].provider.model
            except KeyError:
                raise ValueError(
                    f"oracle profile is not configured: {definition.oracle_profile_id}"
                ) from None
        payload = {
            "mode": definition.mode.value,
            "profile_id": definition.profile_id,
            "model": profile.provider.model,
            "oracle_profile_id": definition.oracle_profile_id,
            "oracle_model": oracle_model,
            "prompt_policy": definition.prompt_policy,
            "tool_names": list(definition.tool_names),
            "reasoning_effort": definition.reasoning_effort.value,
            "limits": limits_payload(definition.limits),
            "description": definition.description,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshot = ModeSnapshot(definition, profile.provider.model, oracle_model, digest)
        if {**snapshot_payload(snapshot), "digest": digest} != {**payload, "digest": digest}:
            raise RuntimeError("mode snapshot payload is unstable")
        return snapshot


def standard_mode_order() -> tuple[AgentMode, ...]:
    return tuple(AgentMode)


def standard_mode_definitions(
    profile_ids: Mapping[AgentMode | str, str],
    *,
    oracle_profile_ids: Mapping[AgentMode | str, str | None] | None = None,
    tools_by_mode: Mapping[AgentMode | str, Sequence[str]] | None = None,
    limits_by_mode: Mapping[AgentMode | str, EngineLimits] | None = None,
) -> tuple[ModeDefinition, ...]:
    profiles = _normalize(profile_ids)
    oracles = _normalize(oracle_profile_ids or {}, allow_partial=True)
    tools = _normalize(tools_by_mode or {}, allow_partial=True)
    limits = _normalize(limits_by_mode or {}, allow_partial=True)
    defaults = _defaults()
    return tuple(
        ModeDefinition(
            mode,
            profiles[mode],
            defaults[mode][0],
            tuple(tools.get(mode, ())),
            defaults[mode][1],
            limits.get(mode, defaults[mode][2]),
            defaults[mode][3],
            oracles.get(mode),
        )
        for mode in AgentMode
    )


def _normalize(values: Mapping[AgentMode | str, object], *, allow_partial: bool = False) -> dict[AgentMode, object]:
    result: dict[AgentMode, object] = {}
    for key, value in values.items():
        mode = AgentMode(key)
        if mode in result:
            raise ValueError(f"duplicate mode mapping: {mode.value}")
        result[mode] = value
    if not allow_partial and set(result) != set(AgentMode):
        raise ValueError("profile_ids must define all standard modes")
    return result


def _defaults() -> dict[AgentMode, tuple[str, ReasoningEffort, EngineLimits, str]]:
    shared_limits = EngineLimits(120, 256, 64, 800_000, 1_000_000)
    return {
        AgentMode.LOW: (
            "direct",
            ReasoningEffort.LOW,
            shared_limits,
            "Execute directly and delegate only when a bounded specialist would materially help.",
        ),
        AgentMode.MEDIUM: (
            "balanced",
            ReasoningEffort.MEDIUM,
            shared_limits,
            "Handle ordinary multi-step work directly and use one specialist when useful.",
        ),
        AgentMode.HIGH: (
            "deliberate",
            ReasoningEffort.HIGH,
            shared_limits,
            "Lead difficult work directly and delegate independent or specialist checks when useful.",
        ),
        AgentMode.ULTRA: (
            "orchestrated",
            ReasoningEffort.XHIGH,
            shared_limits,
            "Orchestrate open-ended work, parallelize independent tasks, and request independent review for risky changes.",
        ),
    }
