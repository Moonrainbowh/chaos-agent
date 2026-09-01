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


@unittest.skipUnless(os.name == "nt", "PowerShell semantics tests are Windows-only")
class RealPowerShellErrorSemanticsTests(LocalRuntimeTestCase):
    async def test_handled_error_does_not_fail_either_dialect(self) -> None:
        await self._assert_success(
            "try { Write-Error 'handled' -ErrorAction Stop } "
            "catch { Write-Output 'recovered' }",
            expected_stdout=b"recovered",
        )

    async def test_handled_error_record_remains_success_stream_data(self) -> None:
        for dialect in self._dialects():
            with self.subTest(dialect=dialect.value):
                result = await self._run(
                    dialect,
                    "try { throw 'handled-data' } catch { Write-Output $_ }",
                )
                self.assertEqual(result.reason, TerminationReason.EXITED)
                self.assertEqual(result.returncode, 0)
                self.assertIn(b"handled-data", result.stdout)
                self.assertNotIn(b"handled-data", result.stderr)

    async def test_silently_continued_error_does_not_fail_either_dialect(
        self,
    ) -> None:
        await self._assert_success(
            "Write-Error 'silent' -ErrorAction SilentlyContinue; "
            "Write-Output 'continued'",
            expected_stdout=b"continued",
        )

    async def test_later_native_success_recovers_native_exit_code(self) -> None:
        await self._assert_success(
            "cmd.exe /d /c exit 7; cmd.exe /d /c exit 0; "
            "Write-Output 'recovered'",
            expected_stdout=b"recovered",
        )

    async def test_native_stderr_with_zero_exit_does_not_fail(self) -> None:
        await self._assert_success(
            'cmd.exe /d /c "echo native-warning 1>&2 & exit /b 0"',
            expected_stderr=b"native-warning",
        )

    async def test_default_powershell_error_stops_before_later_output(
        self,
    ) -> None:
        for dialect in self._dialects():
            with self.subTest(dialect=dialect.value):
                result = await self._run(
                    dialect,
                    "Write-Error 'unhandled'; Write-Output 'continued'",
                )
                self.assertEqual(result.reason, TerminationReason.EXITED)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"unhandled", result.stderr)
                self.assertNotIn(b"continued", result.stdout)

    async def test_native_error_id_cannot_disguise_powershell_error(self) -> None:
        for dialect in self._dialects():
            with self.subTest(dialect=dialect.value):
                result = await self._run(
                    dialect,
                    "Write-Error -Message 'powershell-failure' "
                    "-ErrorId NativeCommandErrorCustom; Write-Output 'continued'",
                )
                self.assertEqual(result.reason, TerminationReason.EXITED)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"powershell-failure", result.stderr)

    async def test_explicit_continue_is_an_intentional_recovery(self) -> None:
        scripts = (
            "Write-Error 'continued-error' -ErrorAction Continue; "
            "Write-Output 'after'",
            "$ErrorActionPreference = 'Continue'; Write-Error 'continued-error'; "
            "Write-Output 'after'",
        )
        for dialect in self._dialects():
            for script in scripts:
                with self.subTest(dialect=dialect.value, script=script):
                    result = await self._run(dialect, script)
                    self.assertEqual(result.returncode, 0)
                    self.assertIn(b"continued-error", result.stderr)
                    self.assertIn(b"after", result.stdout)

    async def test_native_failure_precedes_later_powershell_error(self) -> None:
        for dialect in self._dialects():
            for error_command in ("Write-Error 'ps-error'", "throw 'boom'"):
                with self.subTest(dialect=dialect.value, error=error_command):
                    result = await self._run(
                        dialect,
                        f"cmd.exe /d /c exit 7; {error_command}",
                    )
                    self.assertEqual(result.returncode, 7)
                    self.assertTrue(result.stderr)

    async def _assert_success(
        self,
        script: str,
        *,
        expected_stdout: bytes | None = None,
        expected_stderr: bytes | None = None,
    ) -> None:
        for dialect in self._dialects():
            with self.subTest(dialect=dialect.value):
                result = await self._run(dialect, script)
                self.assertEqual(result.reason, TerminationReason.EXITED)
                self.assertEqual(result.returncode, 0)
                if expected_stdout is not None:
                    self.assertIn(expected_stdout, result.stdout)
                if expected_stderr is not None:
                    self.assertIn(expected_stderr, result.stderr)

    @staticmethod
    def _dialects() -> tuple[ShellDialect, ...]:
        return (
            ShellDialect.POWERSHELL_7,
            ShellDialect.WINDOWS_POWERSHELL_5_1,
        )

    async def _run(self, dialect: ShellDialect, script: str) -> CommandResult:
        resolver = PowerShellRuntimeResolver(dialect)
        try:
            resolver.resolve()
        except RuntimeUnavailable:
            self.skipTest(f"{dialect.value} is unavailable")
        runtime = WindowsLocalRuntime(self.root, powershell=resolver)
        return await runtime.run(
            CommandSpec(cwd=".", shell_script=ShellScript(script, dialect)),
            CancellationToken(),
            None,
        )


if __name__ == "__main__":
    unittest.main()
