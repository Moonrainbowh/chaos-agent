"""Select the local runtime that matches the current host platform."""
from __future__ import annotations

import os
from pathlib import Path

from ._powershell_runtime import PowerShellRuntimeResolver
from .local import WindowsLocalRuntime
from .posix import PosixLocalRuntime


def build_local_runtime(
    root: Path, *, powershell: PowerShellRuntimeResolver | None = None
) -> WindowsLocalRuntime | PosixLocalRuntime:
    """Build a host-native runtime without treating POSIX as Windows."""
    if os.name == "nt":
        return WindowsLocalRuntime(root, powershell=powershell)
    return PosixLocalRuntime(root)
