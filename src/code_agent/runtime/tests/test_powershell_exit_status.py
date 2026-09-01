from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import CommandResult, CommandSpec, TerminationReason


class PowerShellExitStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = WindowsLocalRuntime(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_native_command_exit_code_is_propagated(self) -> None:
        result = await self._run("cmd.exe /d /c exit 7")

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 7)

    async def test_native_failure_is_not_hidden_by_later_powershell_success(self) -> None:
        result = await self._run(
            "cmd.exe /d /c exit 7; Write-Output 'continued after failure'"
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 7)
        self.assertIn(b"continued after failure", result.stdout)

    async def test_powershell_failure_state_becomes_nonzero_exit(self) -> None:
        result = await self._run("Write-Error 'expected runtime test failure'")

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 1)

    async def test_default_powershell_error_stops_later_success(self) -> None:
        result = await self._run(
            "Write-Error 'expected runtime test failure'; "
            "Write-Output 'continued after failure'"
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b"continued after failure", result.stdout)
        self.assertIn(b"expected runtime test failure", result.stderr)

    async def _run(self, script: str) -> CommandResult:
        return await self.runtime.run(
            CommandSpec(cwd=".", powershell_script=script),
            CancellationToken(),
            None,
        )


if __name__ == "__main__":
    unittest.main()
