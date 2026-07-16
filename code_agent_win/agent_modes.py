from __future__ import annotations

import os
from collections.abc import Mapping

from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode, ModeSnapshot
from code_agent.providers.config import ModelProfile


READ_TOOLS = ("read_file", "list_files", "search_text", "git_status", "git_diff")
TYPED_WRITE_TOOLS = READ_TOOLS + ("write_file", "replace_text", "run_verification")
ALL_TOOLS = TYPED_WRITE_TOOLS + ("run_command", "delegate_agent")


def build_mode_registry(
    profiles: Mapping[str, ModelProfile], default_profile: str
) -> tuple[ModeRegistry, dict[AgentMode, str]]:
    if default_profile not in profiles:
        raise ValueError("default mode profile is not configured")
    bindings: dict[AgentMode, str] = {}
    for mode in AgentMode:
        configured = os.getenv(f"CHAOS_MODE_{mode.value.upper()}_PROFILE", default_profile)
        if configured not in profiles:
            raise ValueError(f"mode profile is not configured: {mode.value} -> {configured}")
        bindings[mode] = configured
    tools = {
        AgentMode.LOW: READ_TOOLS,
        AgentMode.MEDIUM: TYPED_WRITE_TOOLS + ("delegate_agent",),
        AgentMode.HIGH: ALL_TOOLS,
        AgentMode.ULTRA: ALL_TOOLS,
    }
    registry = ModeRegistry(
        standard_mode_definitions(bindings, tools_by_mode=tools)
    )
    return registry, bindings


def freeze_mode(
    registry: ModeRegistry,
    profiles: Mapping[str, ModelProfile],
    mode_name: str | None,
) -> ModeSnapshot:
    selected = mode_name or os.getenv("CHAOS_MODE", AgentMode.MEDIUM.value)
    return registry.freeze(AgentMode(selected), profiles)


def mode_prompt(snapshot: ModeSnapshot) -> str:
    definition = snapshot.definition
    return (
        f"Task mode: {definition.mode.value}. Prompt policy: {definition.prompt_policy}. "
        f"Reasoning effort: {definition.reasoning_effort.value}. "
        "Mode changes capability and cost only; it never grants permission."
    )


def default_child_mode(parent_mode: AgentMode) -> AgentMode:
    return {
        AgentMode.LOW: AgentMode.LOW,
        AgentMode.MEDIUM: AgentMode.LOW,
        AgentMode.HIGH: AgentMode.LOW,
        AgentMode.ULTRA: AgentMode.MEDIUM,
    }[parent_mode]
