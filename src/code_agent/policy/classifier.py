from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import FrozenSet, Optional

from code_agent.core.models import ActionRequest

from .models import Capability, RiskLevel


_READ_TOOLS = frozenset(
    {"read_file", "list_files", "search_text", "git_status", "git_diff"}
)
_WRITE_TOOLS = frozenset(
    {"write_file", "replace_text", "create_checkpoint", "restore_checkpoint"}
)
_PROTECTED_PATH_NAMES = frozenset({".env", ".git", ".chaos-agent", ".code-agent"})
_PRIVATE_KEY_NAMES = re.compile(r"(?:^|[_-])(?:id_rsa|id_ecdsa|id_ed25519|private(?:[_-]?key)?)(?:\.[a-z0-9]+)?$", re.IGNORECASE)

_NETWORK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcurl(?:\.exe)?\b",
        r"\biwr\b",
        r"\binvoke-webrequest\b",
        r"\birm\b",
        r"\binvoke-restmethod\b",
        r"\bwget(?:\.exe)?\b",
        r"\bstart-bitstransfer\b",
        r"\bgit\s+(?:clone|fetch|pull)\b",
        r"\bpip(?:3(?:\.\d+)?)?\s+install\b",
        r"\buv\s+(?:add|sync)\b",
        r"\b(?:npm|pnpm)\s+(?:install|i|ci|add|update)\b",
        r"\byarn\s+(?:install|add|upgrade)\b",
    )
)
_CRITICAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bgit\s+reset\b[^;&|\r\n]*--hard\b",
        r"\bgit\s+clean\s+-(?=[a-z]*f)(?=[a-z]*d)[a-z]+\b",
        r"\brm\s+-(?=[a-z]*r)(?=[a-z]*f)[a-z]+\b",
        r"\b(?:del|rmdir)\b[^;&|\r\n]*/s\b",
        r"\bformat(?:\.com)?\b",
        r"\bdiskpart(?:\.exe)?\b",
        r"\bshutdown(?:\.exe)?\b",
        r"\bstop-computer\b",
        r"\brunas(?:\.exe)?\b",
        r"\bsudo\b",
        r"\bset-executionpolicy\b",
    )
)
_REMOVE_ITEM = re.compile(
    r"\b(?:remove-item|rm|ri|del|erase|rd|rmdir)\b(?P<arguments>[^;&|\r\n]*)",
    re.IGNORECASE,
)
_RECURSE_FLAG = re.compile(
    r"(?:^|\s)-(?:r|re|rec|recu|recur|recurs|recurse)"
    r"(?::(?:\$true|true|1))?(?=\s|$)",
    re.IGNORECASE,
)
_FORCE_FLAG = re.compile(
    r"(?:^|\s)-(?:f|fo|for|forc|force)(?::(?:\$true|true|1))?(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_INVOCATION = re.compile(
    r"\b(?:powershell|pwsh)(?:\.exe)?\b(?P<arguments>[^;&|\r\n]*)",
    re.IGNORECASE,
)
_POWERSHELL_FLAG = re.compile(r"(?:^|\s)-(?P<name>[a-z]+)\b", re.IGNORECASE)
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


def _matches_any(command: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(command) is not None for pattern in patterns)


def _is_critical_command(command: str) -> bool:
    if _matches_any(command, _CRITICAL_PATTERNS):
        return True
    for invocation in _POWERSHELL_INVOCATION.finditer(command):
        for flag in _POWERSHELL_FLAG.finditer(invocation.group("arguments")):
            name = flag.group("name").casefold()
            if name in {"e", "ec"} or (
                len(name) >= 2 and "encodedcommand".startswith(name)
            ):
                return True
    for invocation in _REMOVE_ITEM.finditer(command):
        arguments = invocation.group("arguments")
        if (
            _RECURSE_FLAG.search(arguments) is not None
            and _FORCE_FLAG.search(arguments) is not None
        ):
            return True
    return False


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
        if _matches_any(command, _NETWORK_PATTERNS):
            capabilities.add(Capability.NETWORK)
        if _is_critical_command(command):
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
