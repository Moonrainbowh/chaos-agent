from __future__ import annotations

import asyncio
import os
import platform
import socket
from pathlib import Path
from collections.abc import Callable
from urllib.parse import urlsplit

from code_agent.interfaces.diagnostic_view import DiagnosticCheck
from code_agent.workspace.windows_paths import windows_path_support, windows_path_units


class SystemDoctor:
    """Run bounded, diagnostic-only checks against the configured host runtime."""

    def __init__(
        self,
        root: Path,
        *,
        powershell: object,
        git: object | None,
        base_url: str | Callable[[], str],
        timeout_seconds: float = 2.0,
    ) -> None:
        self._root = root.resolve()
        self._powershell = powershell
        self._git = git
        self._base_url = base_url
        self._timeout = timeout_seconds

    async def run(self) -> tuple[DiagnosticCheck, ...]:
        return (
            self._powershell_check(),
            self._git_check(),
            self._workspace_check(),
            self._path_check(),
            await self._endpoint_check(),
            DiagnosticCheck(
                "pass", "Python",
                f"{platform.python_version()} ({platform.machine()})",
            ),
        )

    def _powershell_check(self) -> DiagnosticCheck:
        info = self._powershell.resolve()
        status = "pass" if str(info.version).split(".")[0] == "7" else "warn"
        return DiagnosticCheck(status, "PowerShell", info.summary)

    def _git_check(self) -> DiagnosticCheck:
        if self._git is None:
            return DiagnosticCheck("warn", "Git workspace", "not detected")
        return DiagnosticCheck("pass", "Git workspace", str(self._root))

    def _workspace_check(self) -> DiagnosticCheck:
        if not self._root.is_dir():
            return DiagnosticCheck("fail", "Workspace", "directory is unavailable")
        readable = os.access(self._root, os.R_OK)
        writable = os.access(self._root, os.W_OK)
        status = "pass" if readable and writable else "fail"
        return DiagnosticCheck(
            status,
            "Workspace",
            f"readable={str(readable).lower()}, writable={str(writable).lower()}",
        )

    def _path_check(self) -> DiagnosticCheck:
        support = windows_path_support()
        units = windows_path_units(self._root)
        status = "pass" if units <= support.max_path_chars else "fail"
        return DiagnosticCheck(
            status,
            "Path policy",
            f"workspace={units} UTF-16 units; {support.summary}",
        )

    async def _endpoint_check(self) -> DiagnosticCheck:
        base_url = self._base_url() if callable(self._base_url) else self._base_url
        parsed = urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return DiagnosticCheck("fail", "Provider endpoint", "invalid host")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_tcp_probe, host, port, self._timeout),
                self._timeout + 0.5,
            )
        except (OSError, TimeoutError, asyncio.TimeoutError):
            return DiagnosticCheck(
                "fail", "Provider endpoint", f"TCP connection failed: {host}:{port}"
            )
        return DiagnosticCheck(
            "pass", "Provider endpoint", f"TCP reachable: {host}:{port}"
        )


def _tcp_probe(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return
