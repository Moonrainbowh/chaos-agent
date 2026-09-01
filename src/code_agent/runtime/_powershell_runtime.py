from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import RuntimeUnavailable
from .models import (
    PowerShellRuntimeInfo,
    PowerShellSelection,
    ShellDialect,
)


ExecutableFinder = Callable[[str], str | None]
PowerShellProbe = Callable[[str], tuple[str, str]]
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PROBE_SCRIPT = (
    "[Console]::Out.Write($PSVersionTable.PSEdition + '|' + "
    "$PSVersionTable.PSVersion.ToString())"
)


class PowerShellRuntimeResolver:
    """Resolve one PowerShell dialect once and share it across an application."""

    def __init__(
        self,
        requested: ShellDialect | None = None,
        *,
        finder: ExecutableFinder | None = None,
        probe: PowerShellProbe | None = None,
    ) -> None:
        if requested is not None and requested not in {
            ShellDialect.POWERSHELL_7,
            ShellDialect.WINDOWS_POWERSHELL_5_1,
        }:
            raise ValueError("requested dialect must identify PowerShell")
        if finder is not None and not callable(finder):
            raise TypeError("finder must be callable or None")
        if probe is not None and not callable(probe):
            raise TypeError("probe must be callable or None")
        self.requested = requested
        self._finder = finder or _find_powershell
        self._probe = probe or (lambda executable: _probe_powershell(executable))
        self._resolved: PowerShellRuntimeInfo | None = None

    def resolve(self) -> PowerShellRuntimeInfo:
        if self._resolved is None:
            self._resolved = self._resolve_once()
        return self._resolved

    def _resolve_once(self) -> PowerShellRuntimeInfo:
        if self.requested is ShellDialect.POWERSHELL_7:
            return self._explicit("pwsh", ShellDialect.POWERSHELL_7)
        if self.requested is ShellDialect.WINDOWS_POWERSHELL_5_1:
            return self._explicit(
                "powershell", ShellDialect.WINDOWS_POWERSHELL_5_1
            )
        primary = self._finder("pwsh")
        if primary is not None:
            try:
                return self._candidate(
                    primary,
                    ShellDialect.POWERSHELL_7,
                    PowerShellSelection.AUTO_PRIMARY,
                )
            except RuntimeUnavailable:
                pass
        fallback = self._finder("powershell")
        if fallback is not None:
            try:
                return self._candidate(
                    fallback,
                    ShellDialect.WINDOWS_POWERSHELL_5_1,
                    PowerShellSelection.AUTO_FALLBACK,
                )
            except RuntimeUnavailable:
                pass
        raise RuntimeUnavailable("PowerShell 7 or Windows PowerShell 5.1 was not found")

    def _explicit(
        self, executable_name: str, dialect: ShellDialect
    ) -> PowerShellRuntimeInfo:
        executable = self._finder(executable_name)
        if executable is None:
            raise RuntimeUnavailable(
                f"configured PowerShell dialect {dialect.value} is unavailable"
            )
        return self._candidate(
            executable, dialect, PowerShellSelection.EXPLICIT
        )

    def _candidate(
        self,
        executable: str,
        dialect: ShellDialect,
        selection: PowerShellSelection,
    ) -> PowerShellRuntimeInfo:
        absolute_executable = _absolute_executable(executable)
        try:
            edition, version = self._probe(absolute_executable)
            _validate_probe(dialect, edition, version)
            return PowerShellRuntimeInfo(
                dialect, absolute_executable, selection, edition, version
            )
        except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
            raise RuntimeUnavailable(
                f"PowerShell candidate does not match {dialect.value}"
            ) from None


def _absolute_executable(executable: str) -> str:
    if (
        PureWindowsPath(executable).is_absolute()
        or PurePosixPath(executable).is_absolute()
    ):
        return executable
    return str(Path(executable).resolve(strict=False))


def _find_powershell(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered is not None or os.name != "nt":
        return discovered
    if name == "pwsh":
        candidate = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "PowerShell"
            / "7"
            / "pwsh.exe"
        )
    elif name == "powershell":
        candidate = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
    else:
        return None
    return str(candidate) if candidate.is_file() else None


def _validate_probe(
    dialect: ShellDialect, edition: str, version: str
) -> None:
    components = version.split(".")
    if not components or not all(part.isdigit() for part in components):
        raise ValueError("invalid PowerShell version")
    major = int(components[0])
    if dialect is ShellDialect.POWERSHELL_7:
        if edition != "Core" or major < 7:
            raise ValueError("candidate is not PowerShell 7")
        return
    if edition != "Desktop" or components[:2] != ["5", "1"]:
        raise ValueError("candidate is not Windows PowerShell 5.1")


def _probe_powershell(executable: str) -> tuple[str, str]:
    completed = subprocess.run(
        (
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PROBE_SCRIPT,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeUnavailable("PowerShell probe failed")
    decoded = completed.stdout.decode("ascii", "strict").strip()
    parts = decoded.split("|")
    if len(parts) != 2:
        raise RuntimeUnavailable("PowerShell probe returned malformed output")
    return parts[0], parts[1]


def resolved_powershell_runtime(
    requested: ShellDialect | None,
) -> PowerShellRuntimeResolver:
    resolver = PowerShellRuntimeResolver(requested)
    resolver.resolve()
    return resolver
