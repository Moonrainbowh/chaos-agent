from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class TerminalTitleAndSoundTests(unittest.IsolatedAsyncioTestCase):
    def test_default_and_explicit_project_name(self) -> None:
        app1 = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None
        )
        self.assertTrue(bool(app1.project_name))

        app2 = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=lambda _: None,
            project_name="chaos-17-ai修改word",
        )
        self.assertEqual(app2.project_name, "chaos-17-ai修改word")

    @patch("code_agent.interfaces.tui_presentation.motion_allowed", return_value=True)
    def test_terminal_title_transitions_and_bell(self, _motion) -> None:
        output: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=output.append,
            project_name="my-project",
        )

        # Initial idle title
        app.update_terminal_title(running=False)
        self.assertIn("\x1b]0;my-project\x07", output)

        # Running spinner title
        output.clear()
        app._spinner_index = 0
        app.update_terminal_title(running=True)
        self.assertIn("\x1b]0;⠋ my-project\x07", output)

        # Next spinner frame
        output.clear()
        app._spinner_index = 1
        app.update_terminal_title(running=True)
        self.assertIn("\x1b]0;⠙ my-project\x07", output)

        # Approval requested: bell indicator & sound
        output.clear()
        app.on_approval_requested()
        self.assertIn("\x1b]0;🔔 my-project\x07", output)
        self.assertIn("\a", output)

        # Task running again
        output.clear()
        app.update_terminal_title(running=True)
        self.assertIn("\x1b]0;⠙ my-project\x07", output)

        # Task finished: completed bell indicator & sound
        output.clear()
        app._task_finished_handled = False
        app.on_task_finished()
        self.assertIn("\x1b]0;🔔 my-project\x07", output)
        self.assertIn("\a", output)

        # Reset on exit restores PowerShell
        output.clear()
        app.reset_terminal_title()
        self.assertIn("\x1b]0;PowerShell\x07", output)

    async def test_task_submission_and_completion_updates_title(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(events)),
            ApprovalBroker(),
            write=output.append,
            project_name="test-repo",
        )
        await app.submit("run task")
        await app.wait_idle()

        # Check that running spinner and completed bell title were emitted
        all_output = "".join(output)
        self.assertIn("\x1b]0;⠋ test-repo\x07", all_output)
        self.assertIn("\x1b]0;🔔 test-repo\x07", all_output)
        self.assertIn("\a", all_output)


if __name__ == "__main__":
    unittest.main()
