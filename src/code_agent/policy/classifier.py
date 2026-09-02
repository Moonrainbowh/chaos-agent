from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Optional

from code_agent.core.models import ActionRequest

from ._command_risk import CommandRisk, command_risk, process_risk
from ._path_classification import (
    path_is_outside,
    targets_outside_workspace,
    targets_protected,
)
from .models import Capability, RiskLevel


_READ_TOOLS = frozenset(
    {
        "load_tool_contract", "read_file", "read_code_slices", "list_files", "search_text", "git_status", "git_diff",
        "plan_workspace_edits_v1",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "write_file", "replace_text", "create_checkpoint", "restore_checkpoint",
        "apply_workspace_edit_plan_v1",
    }
)
EDIT_PLAN_RISK_FLAGS = frozenset(
    {
        "dirty",
        "dirty_baseline",
        "non_git_existing",
        "untracked_existing",
        "delete",
        "move",
        "case_only_move",
    }
)
@dataclass(frozen=True)
class ActionClassification:
    capabilities: FrozenSet[Capability] = field(default_factory=frozenset)
    risk: RiskLevel = RiskLevel.LOW
    reason: str = "recognized action"
    known_tool: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


def requires_explicit_edit_plan_approval(risk_flags: Sequence[str]) -> bool:
    """Return whether trusted local plan facts require a visible confirmation."""
    if not isinstance(risk_flags, Sequence) or isinstance(risk_flags, (str, bytes)):
        raise TypeError("risk_flags must be a sequence of strings")
    if any(not isinstance(flag, str) for flag in risk_flags):
        raise TypeError("risk_flags must contain strings")
    unknown = set(risk_flags).difference(EDIT_PLAN_RISK_FLAGS)
    if unknown:
        raise ValueError("risk_flags contain an unknown edit-plan risk")
    return bool(risk_flags)


def _classify_command(
    arguments: Mapping[str, object], workspace_root: Optional[Path]
) -> ActionClassification:
    capabilities = {Capability.EXECUTE, Capability.RAW_SHELL}
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return ActionClassification(
            frozenset(capabilities), RiskLevel.CRITICAL,
            "run_command requires a non-blank string command",
        )
    signals = command_risk(command)
    _apply_command_boundaries(capabilities, signals, workspace_root)
    if signals.critical:
        return ActionClassification(
            frozenset(capabilities), RiskLevel.CRITICAL,
            "command includes a destructive or elevated operation",
        )
    if Capability.PROTECTED_PATH in capabilities:
        reason = "command targets a protected path"
    elif Capability.OUTSIDE_WORKSPACE in capabilities:
        reason = "command targets outside the workspace"
    elif Capability.NETWORK in capabilities:
        reason = "command includes a network-capable operation"
    else:
        reason = "recognized raw local command"
    return ActionClassification(frozenset(capabilities), RiskLevel.HIGH, reason)


def _classify_process(
    arguments: Mapping[str, object], workspace_root: Optional[Path]
) -> ActionClassification:
    capabilities = {Capability.EXECUTE, Capability.RAW_PROCESS}
    program, raw_args = arguments.get("program"), arguments.get("args")
    valid_args = (
        isinstance(raw_args, Sequence)
        and not isinstance(raw_args, (str, bytes))
        and all(isinstance(item, str) for item in raw_args)
    )
    if not isinstance(program, str) or not program.strip() or not valid_args:
        return ActionClassification(
            frozenset(capabilities), RiskLevel.CRITICAL,
            "run_process_v1 requires program text and string args",
        )
    signals = process_risk(program, tuple(raw_args))
    _apply_command_boundaries(capabilities, signals, workspace_root)
    if signals.critical:
        return ActionClassification(
            frozenset(capabilities), RiskLevel.CRITICAL,
            "structured process includes a shell or critical operation",
        )
    if Capability.PROTECTED_PATH in capabilities:
        reason = "structured process targets a protected path"
    elif Capability.OUTSIDE_WORKSPACE in capabilities:
        reason = "structured process targets outside the workspace"
    elif Capability.NETWORK in capabilities:
        reason = "structured process includes a network-capable operation"
    else:
        reason = "recognized structured local process"
    return ActionClassification(frozenset(capabilities), RiskLevel.HIGH, reason)


def _apply_command_boundaries(
    capabilities: set[Capability],
    signals: CommandRisk,
    workspace_root: Optional[Path],
) -> None:
    if signals.protected:
        capabilities.add(Capability.PROTECTED_PATH)
    if any(
        path_is_outside(path.rstrip(",)]"), workspace_root)
        for path in signals.paths
    ):
        capabilities.add(Capability.OUTSIDE_WORKSPACE)
    if signals.network:
        capabilities.add(Capability.NETWORK)


def _classify_named_action(
    name: str,
    request: ActionRequest,
    workspace_root: Optional[Path],
    mcp_risks: Mapping[str, str] | None,
) -> ActionClassification:
    mcp_risk = (mcp_risks or {}).get(name)
    mapping = {
        "read": ({Capability.READ}, RiskLevel.LOW),
        "write": ({Capability.WRITE}, RiskLevel.MEDIUM),
        "network": ({Capability.NETWORK}, RiskLevel.HIGH),
        "critical": (set(), RiskLevel.CRITICAL),
    }
    if mcp_risk is not None:
        if mcp_risk not in mapping:
            return ActionClassification(
                risk=RiskLevel.CRITICAL,
                reason="MCP tool has an unknown risk mapping",
                known_tool=False,
            )
        capabilities, risk = mapping[mcp_risk]
        return ActionClassification(frozenset(capabilities), risk, "configured MCP tool")
    if name in _READ_TOOLS:
        return ActionClassification(frozenset({Capability.READ}), RiskLevel.LOW, "recognized read-only tool")
    if name in _WRITE_TOOLS:
        return ActionClassification(frozenset({Capability.WRITE}), RiskLevel.MEDIUM, "recognized workspace write tool")
    if name == "run_command":
        return _classify_command(request.arguments, workspace_root)
    if name == "run_process_v1":
        return _classify_process(request.arguments, workspace_root)
    if name == "run_verification":
        return ActionClassification(
            frozenset({Capability.EXECUTE, Capability.VERIFICATION}),
            RiskLevel.MEDIUM, "recognized structured local verification",
        )
    return ActionClassification(
        risk=RiskLevel.CRITICAL,
        reason=f"unknown tool: {request.name}",
        known_tool=False,
    )


def classify_action(
    request: ActionRequest, workspace_root: Optional[Path] = None, mcp_risks: Mapping[str, str] | None = None
) -> ActionClassification:
    """Classify an action using conservative tool-name and command heuristics."""

    if not isinstance(request, ActionRequest):
        raise TypeError("request must be an ActionRequest")
    if workspace_root is not None and not isinstance(workspace_root, Path):
        raise TypeError("workspace_root must be a Path or None")

    classified = _classify_named_action(
        request.name.casefold(), request, workspace_root, mcp_risks
    )
    if not classified.known_tool or classified.risk is RiskLevel.CRITICAL:
        return classified
    outside = targets_outside_workspace(request.arguments, workspace_root)
    protected = targets_protected(request.arguments)
    capabilities = set(classified.capabilities)
    risk, reason = classified.risk, classified.reason
    if outside:
        capabilities.add(Capability.OUTSIDE_WORKSPACE)
        risk = RiskLevel.HIGH
        reason = "request explicitly targets outside the workspace"
    if protected:
        capabilities.add(Capability.PROTECTED_PATH)
        risk = RiskLevel.HIGH
        reason = "request targets a protected path"

    return ActionClassification(frozenset(capabilities), risk, reason)
