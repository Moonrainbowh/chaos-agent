from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.orchestration.models import AgentMode
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.runtime.models import (
    PowerShellRuntimeInfo,
    PowerShellSelection,
    ShellDialect,
)
from code_agent.runtime.errors import RuntimeUnavailable
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent_win.cli import run
from code_agent_win.tool_support import windows_system_prompt
from code_agent_win.tools import tool_definitions
from tests.agent_app_test_support import _isolated_application


def _runtime_info() -> PowerShellRuntimeInfo:
    return PowerShellRuntimeInfo(
        ShellDialect.POWERSHELL_7,
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        PowerShellSelection.AUTO_PRIMARY,
        "Core",
        "7.6.5",
    )


def _mode_snapshot() -> object:
    profile = ModelProfile(
        "sol",
        ProviderConfig(
            "https://api.example.test",
            "gpt-sol",
            ApiProtocol.RESPONSES,
            "TEST_KEY",
        ),
        8_000,
        1_000,
    )
    registry = ModeRegistry(
        standard_mode_definitions(
            {mode: "sol" for mode in AgentMode},
            tools_by_mode={mode: ("read_file",) for mode in AgentMode},
        )
    )
    return registry.freeze_runtime(
        "medium",
        {"sol": profile},
        profile_id="sol",
        topology="single",
        reasoning_effort="high",
    )


class PowerShellSurfaceTests(unittest.TestCase):
    def test_prompt_exposes_real_dialect_without_local_path(self) -> None:
        prompt = windows_system_prompt(True, _runtime_info())

        self.assertIn("powershell_7", prompt)
        self.assertIn("PowerShell 7.6.5", prompt)
        self.assertIn("Core", prompt)
        self.assertIn("pwsh.exe", prompt)
        self.assertNotIn(r"C:\Program Files", prompt)
        self.assertIn("no shell parsing", prompt)

    def test_tool_description_uses_the_frozen_runtime(self) -> None:
        run_command = next(
            tool
            for tool in tool_definitions(powershell=_runtime_info())
            if tool.name == "run_command"
        )

        self.assertIn("powershell_7", run_command.description)
        self.assertIn("7.6.5", run_command.description)
        self.assertNotIn(r"C:\Program Files", run_command.description)

    def test_local_status_keeps_the_full_runtime_path(self) -> None:
        info = _runtime_info()
        view = ModePermissionView(
            _mode_snapshot(),  # type: ignore[arg-type]
            PermissionSummary(ApprovalMode.AUTO, r"F:\repo", False, True),
            runtime_summary=info.summary,
        )

        self.assertIn(info.executable, view.lines()[-1])
        self.assertIn("auto_primary", view.lines()[-1])


class PowerShellCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_tui_capability_includes_windows_path_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _isolated_application(Path(temporary).resolve())
            try:
                summary = application.tui._capability.runtime_summary
                self.assertIn("powershell", summary)
                self.assertIn("paths=", summary)
            finally:
                await application.aclose()

    async def test_host_runtime_survives_runtime_and_permission_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _isolated_application(Path(temporary).resolve())
            try:
                initial = application.tui.host_runtime_summary
                await application.runtime_selection.use(
                    reasoning_effort="high", idle=True
                )
                self.assertEqual(application.tui.host_runtime_summary, initial)

                await application.tui.permissions.use("plan", idle=True)
                self.assertEqual(application.tui.host_runtime_summary, initial)
                self.assertIn("paths=", application.tui.host_runtime_summary)
            finally:
                await application.aclose()

    async def test_unavailable_explicit_dialect_has_actionable_diagnostic(self) -> None:
        stderr = io.StringIO()
        with patch(
            "code_agent_win.cli.create_application",
            side_effect=RuntimeUnavailable(
                "configured PowerShell dialect powershell_7 is unavailable"
            ),
        ), contextlib.redirect_stderr(stderr):
            status = await run(("ask", "inspect"))

        self.assertEqual(status, 2)
        self.assertIn("powershell_7 is unavailable", stderr.getvalue())
        self.assertIn("powershell_dialect", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
