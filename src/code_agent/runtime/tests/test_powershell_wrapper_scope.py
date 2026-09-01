from __future__ import annotations

import os
import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from code_agent.runtime.errors import RuntimeUnavailable
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import (
    CommandResult,
    CommandSpec,
    ShellDialect,
    ShellScript,
    TerminationReason,
)

from _local_test_support import LocalRuntimeTestCase


@unittest.skipUnless(os.name == "nt", "PowerShell scope tests are Windows-only")
class RealPowerShellWrapperScopeTests(LocalRuntimeTestCase):
    async def test_powershell_7_native_failure_survives_top_level_return(self) -> None:
        await self._assert_native_failure_survives_return(ShellDialect.POWERSHELL_7)

    async def test_windows_powershell_native_failure_survives_return(self) -> None:
        await self._assert_native_failure_survives_return(
            ShellDialect.WINDOWS_POWERSHELL_5_1
        )

    async def test_powershell_7_error_survives_top_level_return(self) -> None:
        await self._assert_error_survives_return(ShellDialect.POWERSHELL_7)

    async def test_windows_powershell_error_survives_return(self) -> None:
        await self._assert_error_survives_return(
            ShellDialect.WINDOWS_POWERSHELL_5_1
        )

    async def test_powershell_7_accepts_top_level_using(self) -> None:
        await self._assert_top_level_using(ShellDialect.POWERSHELL_7)

    async def test_windows_powershell_accepts_top_level_using(self) -> None:
        await self._assert_top_level_using(ShellDialect.WINDOWS_POWERSHELL_5_1)

    async def test_powershell_7_accepts_top_level_param(self) -> None:
        await self._assert_top_level_param(ShellDialect.POWERSHELL_7)

    async def test_windows_powershell_accepts_top_level_param(self) -> None:
        await self._assert_top_level_param(ShellDialect.WINDOWS_POWERSHELL_5_1)

    async def _assert_native_failure_survives_return(
        self, dialect: ShellDialect
    ) -> None:
        result = await self._run(dialect, "cmd.exe /d /c exit 7; return")

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 7)

    async def _assert_error_survives_return(self, dialect: ShellDialect) -> None:
        result = await self._run(dialect, "Write-Error 'return-error'; return")

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"return-error", result.stderr)

    async def _assert_top_level_using(self, dialect: ShellDialect) -> None:
        result = await self._run(
            dialect,
            "using namespace System.Text\n"
            "[Console]::Out.WriteLine([UTF8Encoding]::new($false).WebName)",
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode("utf-8", "strict").strip(), "utf-8")

    async def _assert_top_level_param(self, dialect: ShellDialect) -> None:
        result = await self._run(
            dialect,
            "param([string]$Value = 'payload-param')\n"
            "[Console]::Out.WriteLine($Value)",
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.decode("utf-8", "strict").strip(),
            "payload-param",
        )

    async def _run(self, dialect: ShellDialect, script: str) -> CommandResult:
        resolver = PowerShellRuntimeResolver(dialect)
        try:
            resolver.resolve()
        except RuntimeUnavailable:
            self.skipTest(f"{dialect.value} is unavailable")
        runtime = WindowsLocalRuntime(self.root, powershell=resolver)
        return await runtime.run(
            CommandSpec(
                cwd=".",
                shell_script=ShellScript(script, dialect),
            ),
            CancellationToken(),
            None,
        )


if __name__ == "__main__":
    unittest.main()
