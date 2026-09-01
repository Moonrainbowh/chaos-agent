from __future__ import annotations

import asyncio
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from code_agent.core.cancellation import CancellationToken
from code_agent.runtime._powershell_runtime import (
    PowerShellRuntimeResolver,
    _find_powershell,
    _probe_powershell,
)
from code_agent.runtime.errors import RuntimeUnavailable
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import (
    CommandSpec,
    PowerShellSelection,
    ShellDialect,
    ShellScript,
    TerminationReason,
)

from _local_test_support import (
    CompletedProcess,
    LocalRuntimeTestCase,
    patch_process_identity_capture,
)


class PowerShellResolverTests(unittest.TestCase):
    def test_auto_primary_is_real_probed_and_cached(self) -> None:
        finder = Mock(return_value="C:\\pwsh.exe")
        probe = Mock(return_value=("Core", "7.6.5"))
        resolver = PowerShellRuntimeResolver(finder=finder, probe=probe)

        first = resolver.resolve()
        second = resolver.resolve()

        self.assertIs(first, second)
        self.assertEqual(first.dialect, ShellDialect.POWERSHELL_7)
        self.assertEqual(first.selection, PowerShellSelection.AUTO_PRIMARY)
        self.assertEqual(first.version, "7.6.5")
        finder.assert_called_once_with("pwsh")
        probe.assert_called_once_with("C:\\pwsh.exe")

    def test_auto_rejects_mismatched_primary_and_uses_fallback(self) -> None:
        finder = Mock(side_effect=("C:\\pwsh.exe", "C:\\powershell.exe"))
        probe = Mock(
            side_effect=(("Desktop", "5.1.1"), ("Desktop", "5.1.26100.1"))
        )

        info = PowerShellRuntimeResolver(finder=finder, probe=probe).resolve()

        self.assertEqual(info.dialect, ShellDialect.WINDOWS_POWERSHELL_5_1)
        self.assertEqual(info.selection, PowerShellSelection.AUTO_FALLBACK)
        self.assertEqual(
            [call.args[0] for call in finder.call_args_list],
            ["pwsh", "powershell"],
        )

    def test_explicit_dialect_never_falls_back(self) -> None:
        finder = Mock(return_value=None)
        resolver = PowerShellRuntimeResolver(
            ShellDialect.POWERSHELL_7, finder=finder, probe=Mock()
        )

        with self.assertRaises(RuntimeUnavailable):
            resolver.resolve()

        finder.assert_called_once_with("pwsh")

    def test_explicit_dialect_rejects_edition_or_version_mismatch(self) -> None:
        for probe_value in (("Desktop", "5.1.1"), ("Core", "6.2.0")):
            with self.subTest(probe=probe_value):
                resolver = PowerShellRuntimeResolver(
                    ShellDialect.POWERSHELL_7,
                    finder=lambda _: "C:\\pwsh.exe",
                    probe=lambda _: probe_value,
                )
                with self.assertRaises(RuntimeUnavailable):
                    resolver.resolve()

    def test_relative_finder_result_is_frozen_as_an_absolute_path(self) -> None:
        expected = str(Path("tools/pwsh.exe").resolve())
        probe = Mock(return_value=("Core", "7.6.5"))

        info = PowerShellRuntimeResolver(
            ShellDialect.POWERSHELL_7,
            finder=lambda _: "tools/pwsh.exe",
            probe=probe,
        ).resolve()

        self.assertEqual(info.executable, expected)
        probe.assert_called_once_with(expected)

    def test_probe_rejects_failure_and_malformed_output(self) -> None:
        failed = subprocess.CompletedProcess(("pwsh",), 1, b"", b"failure")
        malformed = subprocess.CompletedProcess(("pwsh",), 0, b"Core", b"")
        for completed in (failed, malformed):
            with self.subTest(returncode=completed.returncode):
                with patch("subprocess.run", return_value=completed):
                    with self.assertRaises(RuntimeUnavailable):
                        _probe_powershell("pwsh")

    def test_probe_timeout_is_reported_as_unavailable(self) -> None:
        timeout = subprocess.TimeoutExpired(("pwsh",), 5)
        resolver = PowerShellRuntimeResolver(
            finder=lambda _: "pwsh",
            probe=Mock(side_effect=timeout),
        )
        with self.assertRaises(RuntimeUnavailable):
            resolver.resolve()

    def test_probe_uses_fixed_flags_and_devnull(self) -> None:
        completed = subprocess.CompletedProcess(
            ("pwsh",), 0, b"Core|7.6.5", b""
        )
        with patch("subprocess.run", return_value=completed) as run:
            self.assertEqual(_probe_powershell("C:\\pwsh.exe"), ("Core", "7.6.5"))

        args = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(args[:5], (
            "C:\\pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"
        ))
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["timeout"], 5)
        self.assertFalse(options["check"])

    @unittest.skipUnless(os.name == "nt", "Windows fallback paths are Windows-only")
    def test_default_finder_uses_standard_install_path_when_path_is_empty(self) -> None:
        expected = str(Path(r"C:\Apps") / "PowerShell" / "7" / "pwsh.exe")
        with patch.dict(os.environ, {"ProgramFiles": r"C:\Apps"}), patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            return_value=None,
        ), patch("pathlib.Path.is_file", return_value=True):
            discovered = _find_powershell("pwsh")

        self.assertEqual(discovered, expected)


class PowerShellRuntimeTests(LocalRuntimeTestCase):
    async def test_powershell_uses_fixed_noninteractive_flags(self) -> None:
        async def spawn(*args: object, **kwargs: object) -> CompletedProcess:
            return CompletedProcess()

        with patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            return_value="C:\\pwsh.exe",
        ), patch(
            "code_agent.runtime._powershell_runtime._probe_powershell",
            return_value=("Core", "7.6.5"),
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec",
            side_effect=spawn,
        ) as create, patch_process_identity_capture() as capture:
            result = await self.runtime.run(
                CommandSpec(cwd=".", powershell_script="Write-Output ready"),
                CancellationToken(),
                None,
            )

        args, options = create.call_args.args, create.call_args.kwargs
        self.assertEqual(
            args[:-1],
            ("C:\\pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File"),
        )
        self.assertFalse(Path(args[-1]).exists())
        self.assertIs(options["stdin"], asyncio.subprocess.DEVNULL)
        self.assertFalse(options["shell"])
        self.assertEqual(result.reason, TerminationReason.EXITED)
        capture.assert_called_once()

    async def test_missing_powershell_is_unavailable(self) -> None:
        with patch(
            "code_agent.runtime._powershell_runtime.shutil.which", return_value=None
        ), patch("pathlib.Path.is_file", return_value=False):
            with self.assertRaises(RuntimeUnavailable):
                await self.runtime.run(
                    CommandSpec(cwd=".", powershell_script="pwd"),
                    CancellationToken(),
                    None,
                )

    async def test_windows_powershell_is_explicit_fallback(self) -> None:
        async def spawn(*args: object, **kwargs: object) -> CompletedProcess:
            self.assertEqual(args[0], "C:\\powershell.exe")
            return CompletedProcess()

        with patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            side_effect=(None, "C:\\powershell.exe"),
        ) as finder, patch("pathlib.Path.is_file", return_value=False), patch(
            "code_agent.runtime._powershell_runtime._probe_powershell",
            return_value=("Desktop", "5.1.26100.1"),
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec",
            side_effect=spawn,
        ), patch_process_identity_capture():
            await self.runtime.run(
                CommandSpec(cwd=".", powershell_script="Write-Output ready"),
                CancellationToken(),
                None,
            )

        self.assertEqual(
            [call.args[0] for call in finder.call_args_list],
            ["pwsh", "powershell"],
        )

    async def test_typed_script_rejects_frozen_dialect_mismatch(self) -> None:
        resolver = PowerShellRuntimeResolver(
            ShellDialect.POWERSHELL_7,
            finder=lambda _: "C:\\pwsh.exe",
            probe=lambda _: ("Core", "7.6.5"),
        )
        runtime = WindowsLocalRuntime(self.root, powershell=resolver)
        with self.assertRaises(RuntimeUnavailable):
            await runtime.run(
                CommandSpec(
                    cwd=".",
                    shell_script=ShellScript(
                        "Write-Output ready",
                        ShellDialect.WINDOWS_POWERSHELL_5_1,
                    ),
                ),
                CancellationToken(),
                None,
            )


if __name__ == "__main__":
    unittest.main()
