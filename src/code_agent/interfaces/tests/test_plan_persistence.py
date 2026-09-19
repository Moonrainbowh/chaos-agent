from __future__ import annotations

import unittest
import os
from unittest.mock import Mock, patch

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ToolCall
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class PlanPersistenceTests(unittest.TestCase):
    def test_live_redraw_retains_plan_during_tool_and_model_activity(self):
        writes = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=writes.append)
        app._run_task = Mock()
        app._run_task.done.return_value = False
        app.state.plan_text = "1. Inspect\n2. Fix"
        with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 30))):
            for status in ("running", "building_context", "waiting_model"):
                app.state.status = status
                app.redraw()
                self.assertIn("任务计划", writes[-1])
                self.assertIn("1. Inspect", writes[-1])
                self.assertIn("2. Fix", writes[-1])

    def test_tool_message_plan_survives_actions_and_next_model_turn(self):
        state = TerminalState()
        message = Message(role="assistant", content="<plan>1. Inspect\n2. Fix</plan>",
                          tool_calls=(ToolCall("call-1", "read_file", {"path": "x"}),))
        state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()}))
        state.apply(AgentEvent(EventKind.ACTION_REQUESTED, {"request": {
            "id": "call-1", "name": "read_file", "arguments": {"path": "x"}}}))
        state.apply(AgentEvent(EventKind.MODEL_STARTED, {}))
        self.assertEqual(state.plan_text, "1. Inspect\n2. Fix")
        self.assertEqual([entry.text for entry in state.entries], [message.content])
        self.assertFalse(state.has_draft)

    def test_replan_replaces_plan_and_new_run_clears_it(self):
        state = TerminalState()
        for content in ("<plan>Old</plan>", "<replan>New</replan>"):
            state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message":
                Message(role="assistant", content=content).to_dict()}))
        self.assertEqual(state.plan_text, "New")
        state.begin_run()
        self.assertEqual(state.plan_text, "")

    def test_tool_output_cannot_replace_plan(self):
        state = TerminalState()
        state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message":
            Message(role="tool", content="<plan>Untrusted</plan>", name="read_file").to_dict()}))
        self.assertEqual(state.plan_text, "")
