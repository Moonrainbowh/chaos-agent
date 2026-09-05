from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_theme import Theme
from code_agent.interfaces.tests._support import FakeEngine
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp


class AppearanceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_startup_theme_switch_and_exit_without_provider(self):
        output = []
        capability = SimpleNamespace(
            permission=SimpleNamespace(approval_mode=SimpleNamespace(value="plan"),
                                       allow_workspace_write=False, allow_network=False),
            mode=SimpleNamespace(model="offline-model", effective_reasoning_effort="medium"),
            runtime_summary="PowerShell 7 via local-test · paths=extended",
        )
        with patch.dict(os.environ, {"CHAOS_THEME": "aurora"}):
            app = ModeAwareWindowsTerminalApp(
                AgentController(FakeEngine(())), ApprovalBroker(), capability=capability,
                project_name="appearance-test", write=output.append,
            )
        self.assertEqual(app.theme, Theme.AURORA)
        keys = (":theme ember", "\r", ":theme mono", "\r", ":exit", "\r")
        with patch("code_agent.interfaces.windows_tui.read_key", side_effect=keys):
            await app.run()
        rendered = "".join(output)
        self.assertIn("CHAOS AGENT", rendered)
        self.assertIn("offline-model", rendered)
        self.assertIn("permission: plan", rendered)
        self.assertIn("Appearance · ember", rendered)
        self.assertIn("Appearance · mono", rendered)
        self.assertEqual(app.theme, Theme.MONO)
        self.assertIsNone(app._run_task)
        self.assertIsNone(app._tail_geometry)
        self.assertTrue(app._visual_task.done())
        self.assertNotIn("\x1b[?1049h", rendered)


if __name__ == "__main__":
    unittest.main()
