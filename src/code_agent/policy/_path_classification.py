from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import Optional


_PROTECTED_PATH_NAMES = frozenset(
    {".env", ".git", ".chaos-agent", ".code-agent", "chaos-agent-workspaces"}
)
_PRIVATE_KEY_NAMES = re.compile(
    r"(?:^|[_-])(?:id_rsa|id_ecdsa|id_ed25519|private(?:[_-]?key)?)(?:\.[a-z0-9]+)?$",
    re.IGNORECASE,
)
_PATH_KEY_WORDS = frozenset(
    {"path", "cwd", "file", "directory", "root", "source", "destination", "target"}
)


def path_is_outside(raw_path: str, workspace_root: Optional[Path]) -> bool:
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


def targets_outside_workspace(
    arguments: Mapping[str, object], workspace_root: Optional[Path]
) -> bool:
    for key, value in arguments.items():
        normalized = key.casefold().replace("-", "_")
        if normalized in {"outside_workspace", "allow_outside_workspace"}:
            if value is True:
                return True
        if _is_path_key(key) and _path_value_is_outside(value, workspace_root):
            return True
        if isinstance(value, Mapping) and targets_outside_workspace(
            value, workspace_root
        ):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and any(
            isinstance(item, Mapping)
            and targets_outside_workspace(item, workspace_root)
            for item in value
        ):
            return True
    return False


def targets_protected(arguments: Mapping[str, object]) -> bool:
    for key, value in arguments.items():
        if _is_path_key(key) and _path_value_is_protected(value):
            return True
        if isinstance(value, Mapping) and targets_protected(value):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and any(
            isinstance(item, Mapping) and targets_protected(item) for item in value
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


def _pure_windows_path_is_outside(raw_path: str, workspace_root: Path) -> bool:
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


def _path_value_is_outside(value: object, workspace_root: Optional[Path]) -> bool:
    if isinstance(value, str):
        return path_is_outside(value, workspace_root)
    if isinstance(value, Mapping):
        return targets_outside_workspace(value, workspace_root)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_path_value_is_outside(item, workspace_root) for item in value)
    return False


def _path_value_is_protected(value: object) -> bool:
    if isinstance(value, str):
        path = PureWindowsPath(value)
        return any(
            part.casefold() in _PROTECTED_PATH_NAMES for part in path.parts
        ) or bool(_PRIVATE_KEY_NAMES.search(path.name))
    if isinstance(value, Mapping):
        return targets_protected(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_path_value_is_protected(item) for item in value)
    return False
