from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import WindowsLongPathError


LEGACY_SAFE_PATH_CHARS = 240
EXTENDED_SAFE_PATH_CHARS = 32_000


@dataclass(frozen=True)
class WindowsPathSupport:
    enabled: bool
    max_path_chars: int
    summary: str


@lru_cache(maxsize=1)
def windows_path_support() -> WindowsPathSupport:
    """Return the process-lifetime Windows long-path policy snapshot."""
    if os.name != "nt":
        return WindowsPathSupport(
            True,
            EXTENDED_SAFE_PATH_CHARS,
            "paths=native",
        )
    enabled = _read_long_paths_enabled()
    if enabled:
        return WindowsPathSupport(
            True,
            EXTENDED_SAFE_PATH_CHARS,
            "paths=extended<=32000 UTF-16 units (LongPathsEnabled=1)",
        )
    return WindowsPathSupport(
        False,
        LEGACY_SAFE_PATH_CHARS,
        f"paths=legacy-safe<={LEGACY_SAFE_PATH_CHARS} UTF-16 units "
        "(LongPathsEnabled=0)",
    )


def require_supported_windows_path(
    path: str | os.PathLike[str],
    *,
    operation: str,
) -> None:
    """Fail before Win32 APIs can truncate or ambiguously reject a path."""
    if os.name != "nt":
        return
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise TypeError("path must be text")
    measured = windows_path_units(raw)
    status = windows_path_support()
    if measured <= status.max_path_chars:
        return
    if status.enabled:
        raise WindowsLongPathError(
            f"{operation} path length {measured} UTF-16 code units exceeds "
            f"the supported Windows limit {status.max_path_chars}"
        )
    raise WindowsLongPathError(
        f"{operation} path length {measured} UTF-16 code units exceeds the "
        f"Windows legacy-safe limit {status.max_path_chars} while "
        "LongPathsEnabled=0. "
        "Enable Win32 long paths and restart Windows before using this path."
    )


def windows_path_units(path: str | os.PathLike[str]) -> int:
    """Measure an absolute path as Windows UTF-16 code units, excluding NUL."""
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise TypeError("path must be text")
    absolute = os.path.abspath(Path(raw).expanduser())
    measured = _without_device_prefix(absolute)
    return len(measured.encode("utf-16-le", errors="surrogatepass")) // 2


def _read_long_paths_enabled() -> bool:
    import winreg

    key_path = r"SYSTEM\CurrentControlSet\Control\FileSystem"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return value == 1


def _without_device_prefix(path: str) -> str:
    folded = path.casefold()
    if folded.startswith("\\\\?\\unc\\"):
        return "\\\\" + path[8:]
    if folded.startswith("\\\\?\\"):
        return path[4:]
    return path
