from __future__ import annotations

from typing import Mapping, cast

from code_agent.core.limits import EngineLimits

from .models import (
    AgentMode,
    ModeDefinition,
    ModeSnapshot,
    ReasoningEffort,
)


def limits_payload(limits: EngineLimits) -> dict[str, int]:
    return {
        "max_agent_rounds": limits.max_agent_rounds,
        "max_tool_calls": limits.max_tool_calls,
        "max_tool_calls_per_round": limits.max_tool_calls_per_round,
        "max_total_tokens": limits.max_total_tokens,
        "max_assistant_chars": limits.max_assistant_chars,
    }


def snapshot_payload(snapshot: ModeSnapshot) -> dict[str, object]:
    definition = snapshot.definition
    return {
        "mode": definition.mode.value,
        "profile_id": definition.profile_id,
        "model": snapshot.model,
        "oracle_profile_id": definition.oracle_profile_id,
        "oracle_model": snapshot.oracle_model,
        "prompt_policy": definition.prompt_policy,
        "tool_names": list(definition.tool_names),
        "reasoning_effort": definition.reasoning_effort.value,
        "limits": limits_payload(definition.limits),
        "description": definition.description,
        "digest": snapshot.digest,
    }


def snapshot_from_payload(data: Mapping[str, object]) -> ModeSnapshot:
    limits = cast(Mapping[str, object], data["limits"])
    definition = ModeDefinition(
        AgentMode(cast(str, data["mode"])),
        cast(str, data["profile_id"]),
        cast(str, data["prompt_policy"]),
        tuple(cast(list[str], data["tool_names"])),
        ReasoningEffort(cast(str, data["reasoning_effort"])),
        EngineLimits(**cast(dict[str, int], limits)),
        cast(str, data["description"]),
        cast(str | None, data.get("oracle_profile_id")),
    )
    return ModeSnapshot(
        definition,
        cast(str, data["model"]),
        cast(str | None, data.get("oracle_model")),
        cast(str, data["digest"]),
    )
