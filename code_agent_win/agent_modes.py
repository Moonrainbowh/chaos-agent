from __future__ import annotations

import os
from collections.abc import Mapping

from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import (
    AgentMode,
    AgentRole,
    ModeSnapshot,
    ReasoningEffort,
    RuntimeReasoningEffort,
)
from code_agent.providers.config import ModelProfile


READ_TOOLS = (
    "read_file",
    "list_files",
    "search_text",
    "git_status",
    "git_diff",
    "search_threads",
    "read_thread",
    "plan_workspace_edits_v1",
)
TYPED_WRITE_TOOLS = READ_TOOLS + (
    "write_file", "replace_text", "apply_workspace_edit_plan_v1",
    "run_verification", "run_process_v1",
)
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
    tools = {mode: ALL_TOOLS for mode in AgentMode}
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
        f"Runtime profile: {snapshot.profile_id}. Topology: {snapshot.topology.value}. "
        f"Reasoning effort: {snapshot.effective_reasoning_effort}. "
        f"Orchestration policy: {definition.description} "
        "Mode changes capability and cost only; it never grants permission."
    )


def runtime_effort_for_mode(effort: ReasoningEffort) -> RuntimeReasoningEffort:
    if effort is ReasoningEffort.MINIMAL:
        return RuntimeReasoningEffort.LOW
    return RuntimeReasoningEffort(effort.value)


def main_tools_for_mode(
    mode_tools: tuple[str, ...], plugin_tools: tuple[str, ...]
) -> tuple[str, ...]:
    return mode_tools + plugin_tools


def child_mode_for_role(role: AgentRole) -> AgentMode:
    try:
        return {
            AgentRole.SEARCH: AgentMode.LOW,
            AgentRole.LIBRARIAN: AgentMode.LOW,
            AgentRole.SUBAGENT: AgentMode.MEDIUM,
            AgentRole.REVIEW: AgentMode.HIGH,
            AgentRole.ORACLE: AgentMode.HIGH,
        }[role]
    except KeyError:
        raise ValueError(f"role has no child profile route: {role.value}") from None
