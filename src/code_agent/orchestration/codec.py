from __future__ import annotations

from typing import Mapping, cast

from code_agent.core.limits import EngineLimits

from .models import (
    AgentTopology,
    AgentMode,
    ModeDefinition,
    ModeSnapshot,
    ReasoningEffort,
    RuntimeReasoningEffort,
    RuntimeSelection,
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
    payload: dict[str, object] = {
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
    if snapshot.runtime_selection is not None:
        payload["runtime_selection"] = runtime_selection_payload(
            snapshot.runtime_selection
        )
    return payload


def runtime_selection_payload(
    selection: RuntimeSelection,
) -> dict[str, object]:
    return {
        "topology": selection.topology.value,
        "profile_id": selection.profile_id,
        "model": selection.model,
        "api_protocol": selection.api_protocol,
        "reasoning_effort": selection.reasoning_effort.value,
        "max_output_tokens": selection.max_output_tokens,
        "legacy_mode": selection.legacy_mode.value,
        "digest": selection.digest,
    }


def runtime_selection_from_payload(
    data: Mapping[str, object],
) -> RuntimeSelection:
    return RuntimeSelection(
        AgentTopology(cast(str, data["topology"])),
        cast(str, data["profile_id"]),
        cast(str, data["model"]),
        cast(str, data["api_protocol"]),
        RuntimeReasoningEffort(cast(str, data["reasoning_effort"])),
        cast(int, data["max_output_tokens"]),
        AgentMode(cast(str, data["legacy_mode"])),
        cast(str, data["digest"]),
    )


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
    runtime_payload = data.get("runtime_selection")
    if runtime_payload is not None and not isinstance(runtime_payload, Mapping):
        raise TypeError("runtime_selection must be a mapping")
    runtime_selection = (
        None
        if runtime_payload is None
        else runtime_selection_from_payload(runtime_payload)
    )
    return ModeSnapshot(
        definition,
        cast(str, data["model"]),
        cast(str | None, data.get("oracle_model")),
        cast(str, data["digest"]),
        runtime_selection,
    )
