from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import FrozenSet, Optional

from code_agent.core.models import ActionRequest

from ._command_risk import command_risk, process_risk
from .models import Capability, RiskLevel


_READ_TOOLS = frozenset(
    {
        "read_file", "list_files", "search_text", "git_status", "git_diff",
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
_PROTECTED_PATH_NAMES = frozenset(
    {".env", ".git", ".chaos-agent", ".code-agent", "chaos-agent-workspaces"}
)
_PRIVATE_KEY_NAMES = re.compile(r"(?:^|[_-])(?:id_rsa|id_ecdsa|id_ed25519|private(?:[_-]?key)?)(?:\.[a-z0-9]+)?$", re.IGNORECASE)

_PATH_KEY_WORDS = frozenset(
    {"path", "cwd", "file", "directory", "root", "source", "destination", "target"}
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


def _is_path_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    words = re.split(r"[^A-Za-z0-9]+", separated.casefold())
    return any(word in _PATH_KEY_WORDS for word in words)


def _native_path_is_outside(raw_path: str, workspace_root: Path) -> bool:
    root = workspace_root.resolve(strict=False)
    native_path = raw_path if os.name == "nt" else raw_path.replace("\\", os.sep)
    candidate = Path(native_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(root)
        common = os.path.commonpath((str(root), str(candidate)))
    except (OSError, ValueError):
        return True
    return os.path.normcase(common) != os.path.normcase(str(root))


def _pure_windows_path_is_outside(
    raw_path: str, workspace_root: Path
) -> bool:
    root = PureWindowsPath(ntpath.normpath(str(workspace_root)))
    candidate = PureWindowsPath(ntpath.normpath(raw_path))
    if not root.is_absolute():
        return True
    try:
        candidate.relative_to(root)
        common = ntpath.commonpath((str(root), str(candidate)))
    except ValueError:
        return True
    return ntpath.normcase(common) != ntpath.normcase(str(root))


def _path_is_outside(raw_path: str, workspace_root: Optional[Path]) -> bool:
    raw_path = raw_path.strip()
    if not raw_path:
        return False

    windows_path = PureWindowsPath(raw_path)
    native_path = Path(raw_path if os.name == "nt" else raw_path.replace("\\", os.sep))
    has_parent = ".." in windows_path.parts or ".." in native_path.parts
    if workspace_root is None:
        return windows_path.is_absolute() or native_path.is_absolute() or has_parent
    if windows_path.is_absolute() and os.name != "nt":
        return _pure_windows_path_is_outside(raw_path, workspace_root)
    return _native_path_is_outside(raw_path, workspace_root)


def _path_value_is_outside(value: object, workspace_root: Optional[Path]) -> bool:
    if isinstance(value, str):
        return _path_is_outside(value, workspace_root)
    if isinstance(value, Mapping):
        return _targets_outside_workspace(value, workspace_root)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_path_value_is_outside(item, workspace_root) for item in value)
    return False


def _targets_outside_workspace(
    arguments: Mapping[str, object], workspace_root: Optional[Path]
) -> bool:
    for key, value in arguments.items():
        normalized = key.casefold().replace("-", "_")
        if normalized in {"outside_workspace", "allow_outside_workspace"}:
            if value is True:
                return True
        if _is_path_key(key) and _path_value_is_outside(value, workspace_root):
            return True
        if isinstance(value, Mapping) and _targets_outside_workspace(
            value, workspace_root
        ):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and any(
            isinstance(item, Mapping)
            and _targets_outside_workspace(item, workspace_root)
            for item in value
        ):
            return True
    return False


def _targets_protected(arguments: Mapping[str, object]) -> bool:
    for key, value in arguments.items():
        if _is_path_key(key) and _path_value_is_protected(value):
            return True
        if isinstance(value, Mapping) and _targets_protected(value):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and any(
            isinstance(item, Mapping) and _targets_protected(item) for item in value
        ):
            return True
    return False


def _path_value_is_protected(value: object) -> bool:
    if isinstance(value, str):
        parts = PureWindowsPath(value).parts
        return any(part.casefold() in _PROTECTED_PATH_NAMES for part in parts) or bool(_PRIVATE_KEY_NAMES.search(PureWindowsPath(value).name))
    if isinstance(value, Mapping):
        return _targets_protected(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_path_value_is_protected(item) for item in value)
    return False


def classify_action(
    request: ActionRequest, workspace_root: Optional[Path] = None, mcp_risks: Mapping[str, str] | None = None
) -> ActionClassification:
    """Classify an action using conservative tool-name and command heuristics."""

    if not isinstance(request, ActionRequest):
        raise TypeError("request must be an ActionRequest")
    if workspace_root is not None and not isinstance(workspace_root, Path):
        raise TypeError("workspace_root must be a Path or None")

    name = request.name.casefold()
    outside = _targets_outside_workspace(request.arguments, workspace_root)
    protected = _targets_protected(request.arguments)

    mcp_risk = (mcp_risks or {}).get(name)
    if mcp_risk is not None:
        mapping = {"read": ({Capability.READ}, RiskLevel.LOW), "write": ({Capability.WRITE}, RiskLevel.MEDIUM), "network": ({Capability.NETWORK}, RiskLevel.HIGH), "critical": (set(), RiskLevel.CRITICAL)}
        try: capabilities, risk = mapping[mcp_risk]
        except KeyError: return ActionClassification(risk=RiskLevel.CRITICAL, reason="MCP tool has an unknown risk mapping", known_tool=False)
        reason = "configured MCP tool"
    elif name in _READ_TOOLS:
        capabilities = {Capability.READ}
        risk = RiskLevel.LOW
        reason = "recognized read-only tool"
    elif name in _WRITE_TOOLS:
        capabilities = {Capability.WRITE}
        risk = RiskLevel.MEDIUM
        reason = "recognized workspace write tool"
    elif name == "run_command":
        capabilities = {Capability.EXECUTE, Capability.RAW_SHELL}
        command = request.arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return ActionClassification(
                frozenset(capabilities),
                RiskLevel.CRITICAL,
                "run_command requires a non-blank string command",
            )
        signals = command_risk(command)
        if signals.network:
            capabilities.add(Capability.NETWORK)
        if signals.critical:
            return ActionClassification(
                frozenset(capabilities),
                RiskLevel.CRITICAL,
                "command includes a destructive or elevated operation",
            )
        if Capability.NETWORK in capabilities:
            risk = RiskLevel.HIGH
            reason = "command includes a network-capable operation"
        else:
            risk = RiskLevel.HIGH
            reason = "command execution always requires approval"
    elif name == "run_process_v1":
        capabilities = {Capability.EXECUTE, Capability.RAW_PROCESS}
        program = request.arguments.get("program")
        raw_args = request.arguments.get("args")
        if (
            not isinstance(program, str)
            or not program.strip()
            or not isinstance(raw_args, Sequence)
            or isinstance(raw_args, (str, bytes))
            or not all(isinstance(item, str) for item in raw_args)
        ):
            return ActionClassification(
                frozenset(capabilities),
                RiskLevel.CRITICAL,
                "run_process_v1 requires program text and string args",
            )
        signals = process_risk(program, tuple(raw_args))
        if signals.network:
            capabilities.add(Capability.NETWORK)
        if signals.critical:
            return ActionClassification(
                frozenset(capabilities),
                RiskLevel.CRITICAL,
                "structured process includes a shell or critical operation",
            )
        risk = RiskLevel.HIGH
        reason = (
            "structured process includes a network-capable operation"
            if signals.network
            else "structured process execution always requires approval"
        )
    elif name == "run_verification":
        capabilities = {Capability.EXECUTE, Capability.VERIFICATION}
        risk = RiskLevel.MEDIUM
        reason = "recognized structured local verification"
    else:
        return ActionClassification(
            risk=RiskLevel.CRITICAL,
            reason=f"unknown tool: {request.name}",
            known_tool=False,
        )

    if outside:
        capabilities.add(Capability.OUTSIDE_WORKSPACE)
        risk = RiskLevel.HIGH
        reason = "request explicitly targets outside the workspace"
    if protected:
        capabilities.add(Capability.PROTECTED_PATH)
        risk = RiskLevel.HIGH
        reason = "request targets a protected path"

    return ActionClassification(frozenset(capabilities), risk, reason)
