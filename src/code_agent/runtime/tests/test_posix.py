from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.runtime.models import CommandSpec, ShellDialect, ShellScript, TerminationReason
from code_agent.runtime.posix import PosixLocalRuntime
from code_agent_win.process_actions import run_powershell_action
from code_agent_win.tools import tool_definitions


@unittest.skipUnless(os.name == "posix", "POSIX runtime required")
class PosixLocalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = PosixLocalRuntime(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_argv_runs_in_the_workspace_with_sanitized_environment(self) -> None:
        result = await self.runtime.run(
            CommandSpec(cwd=Path("."), argv=(sys.executable, "-c", "print('ready')")),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), b"ready")
        self.assertEqual(result.cwd, ".")

    async def test_posix_script_uses_sh(self) -> None:
        result = await self.runtime.run(
            CommandSpec(
                cwd=Path("."),
                shell_script=ShellScript("printf ready", ShellDialect.POSIX_SH),
            ),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.stdout, b"ready")

    async def test_run_command_bridge_uses_posix_shell_and_advertises_it(self) -> None:
        result = await run_powershell_action(
            ActionRequest("call", "run_command", {"command": "printf bridged"}),
            self.runtime,
            CancellationToken(),
            None,
        )

        description = next(
            tool.description
            for tool in tool_definitions(shell_dialect=ShellDialect.POSIX_SH)
            if tool.name == "run_command"
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["stdout"], "bridged")
        self.assertIn("POSIX sh", description)

    async def test_timeout_terminates_the_process_group(self) -> None:
        result = await self.runtime.run(
            CommandSpec(
                cwd=Path("."),
                argv=(sys.executable, "-c", "import time; time.sleep(10)"),
                timeout_s=0.1,
            ),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.TIMEOUT)
        self.assertIsNone(result.returncode)

    async def test_cancellation_terminates_the_process_group(self) -> None:
        token = CancellationToken()
        asyncio.get_running_loop().call_later(0.1, token.cancel, "user stop")

        result = await self.runtime.run(
            CommandSpec(
                cwd=Path("."),
                argv=(sys.executable, "-c", "import time; time.sleep(10)"),
            ),
            token,
            None,
        )

        self.assertEqual(result.reason, TerminationReason.CANCELLED)
        self.assertEqual(result.cancellation_reason, "user stop")
