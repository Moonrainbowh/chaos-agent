import sys
import unittest
import os
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind, Usage
from code_agent.context_windows.policy import QueuedContextBoundary
from code_agent.interfaces.terminal_state import TerminalState, ApprovalBroker
from code_agent.interfaces.terminal_status import status_context
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.tests._support import FakeEngine


class ContextBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_slate_redraw_and_new_conversation_preserve_budget_boundaries(self):
        output = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.current_thread_id = "thread-one"
        app.state.total_tokens = 4_000_000
        app.state.apply(AgentEvent(EventKind.CONTEXT_BUILT, {
            "prompt_tokens": 192_000, "window_input_cap": 256_000,
            "window_number": 1, "task_tokens_spent": 4_000_000,
            "task_token_limit": 5_000_000, "task_tokens_reserved": 0,
        }))
        with patch("shutil.get_terminal_size", return_value=os.terminal_size((220, 40))):
            app.redraw()
            rendered = "".join(output)
            self.assertIn("window 2: 192.0k tokens (75%)", rendered)
            self.assertIn("task 4,000,000/5,000,000", rendered)
            output.clear()
            self.assertTrue(await app.submit("/new"))
            app.redraw()
        self.assertIsNone(app.current_thread_id)
        self.assertIsNone(app.state.context_budget.window_input_cap)
        self.assertNotIn("4,000,000", "".join(output))

    def test_input_occupancy_and_task_spend_use_different_denominators(self):
        state=TerminalState()
        state.apply(AgentEvent(EventKind.CONTEXT_BUILT, {"prompt_tokens":192000,"window_input_cap":256000,
            "window_number":1,"task_tokens_spent":4000000,"task_token_limit":5000000,"task_tokens_reserved":0}))
        budget=state.context_budget
        line=status_context("model",None,0,tokens=budget.context_tokens,context_window=budget.window_input_cap,
            window_number=budget.window_number,task_spent=budget.task_tokens_spent,task_limit=budget.task_token_limit)
        self.assertIn("75%",line)
        self.assertIn("task 4,000,000/5,000,000",line)
        self.assertIn(">=80%",line)
        state.apply(AgentEvent(EventKind.MODEL_EVENT,{"event":ModelEvent(ModelEventKind.USAGE,usage=Usage(190000,100,180000)).to_dict()}))
        self.assertEqual(budget.context_tokens,190000)
        self.assertEqual(budget.task_tokens_spent,4190100)

    async def test_manual_boundary_reports_queued_without_false_compaction(self):
        class Engine(FakeEngine):
            async def compact_context(self, thread, cancellation):
                return QueuedContextBoundary("request-one")
        app=WindowsTerminalApp(AgentController(Engine(())),ApprovalBroker(),write=lambda _:None)
        app.current_thread_id="thread-one"
        self.assertTrue(await app.submit("/compact"))
        self.assertIn("queued",app.state.entries[-1].text)
        self.assertNotIn("persisted",app.state.entries[-1].text)
