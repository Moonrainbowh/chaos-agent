from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import ExitGuard, MAX_PASTE_BYTES, paste_event
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entries, render_entry, render_live_tail
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_status import status_context, status_presentation
from code_agent.interfaces.terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class WindowsTerminalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_cancellation_is_a_pause_not_an_error_entry(self) -> None:
        class CancelledTasks:
            async def events(self, task_id: str, text: str):
                if False:
                    yield None
                raise CancellationError("user requested pause")

        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), tasks=CancelledTasks(), write=lambda _: None,
        )

        await app._consume_task("task-1", "continue")

        self.assertEqual(app.state.status, "paused")
        self.assertFalse(any(entry.kind is DisplayKind.ERROR for entry in app.state.entries))

    async def test_follow_up_continues_the_current_nonterminal_task(self) -> None:
        class ContinuingTasks:
            async def start(self, _: str) -> object:
                raise AssertionError("follow-up must not create a new task")

            async def events(self, task_id: str, text: str):
                self.seen = (task_id, text)
                yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": task_id, "status": "verifying"})

        tasks = ContinuingTasks()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), tasks=tasks, write=lambda _: None,
        )
        app.active_task_id = "task-1"
        app._run_task = asyncio.create_task(asyncio.sleep(0))
        await app._run_task

        self.assertTrue(await app.submit("follow up"))
        await app.wait_idle()

        self.assertEqual(tasks.seen, ("task-1", "follow up"))
        self.assertEqual(app.active_task_id, "task-1")

    async def test_user_submit_cancels_and_awaits_peer_turn_before_reusing_slot(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-user"}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-user"}),
        )
        app = WindowsTerminalApp(
            AgentController(FakeEngine(events)),
            ApprovalBroker(),
            write=lambda _: None,
        )
        token = CancellationToken()

        async def peer_turn() -> None:
            await token.wait_async()
            token.raise_if_cancelled()

        previous = asyncio.create_task(peer_turn())
        app._token = token
        app._peer_run_task = previous
        app._run_task = previous

        self.assertTrue(await app.submit("user takes over"))
        replacement = app._run_task
        await app.wait_idle()

        self.assertTrue(previous.done())
        self.assertIsNot(replacement, previous)
        self.assertIsNone(app._peer_run_task)

    async def test_user_submit_force_cancels_unresponsive_peer_turn(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-user"}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-user"}),
        )
        app = WindowsTerminalApp(
            AgentController(FakeEngine(events)),
            ApprovalBroker(),
            write=lambda _: None,
        )
        never = asyncio.Event()
        previous = asyncio.create_task(never.wait())
        app._token = CancellationToken()
        app._peer_run_task = previous
        app._run_task = previous

        self.assertTrue(await asyncio.wait_for(app.submit("user takes over"), 1.0))
        replacement = app._run_task
        await app.wait_idle()

        self.assertTrue(previous.cancelled())
        self.assertIsNot(replacement, previous)
        self.assertIsNone(app._peer_run_task)

    async def test_foreground_start_conflict_is_rendered_without_exiting(self) -> None:
        class ConflictingTasks:
            async def start(self, _: str) -> object:
                raise RuntimeError("a foreground task is already active")

        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            tasks=ConflictingTasks(), write=lambda _: None,
        )

        self.assertFalse(await app.submit("inspect"))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)
        self.assertIn("already active", app.state.entries[-1].text)

    async def test_submit_appends_user_and_completed_agent_entries(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}),
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=output.append)
        self.assertTrue(await app.submit("inspect")); await app.wait_idle()
        self.assertEqual([entry.kind for entry in app.state.entries], [DisplayKind.USER, DisplayKind.AGENT])
        self.assertEqual(app.state.status, "completed")
        self.assertNotIn("[2J", "".join(output))
        await asyncio.sleep(0)

    async def test_streamed_answer_is_written_once_after_completion(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="full ").to_dict()}),
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="answer").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=output.append)
        await app.submit("inspect"); await app.wait_idle()
        rendered = "".join(output)
        plain = _plain(rendered)
        self.assertEqual(plain.count("✦ Chaos Agent\n  full answer"), 1)
        self.assertNotIn("◆ full \n", plain)

    async def test_raw_reasoning_is_not_written(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.REASONING_DELTA, text="private work").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {}),
        )
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=lambda _: None)
        await app.submit("inspect"); await app.wait_idle()
        self.assertNotIn("private work", "\n".join(entry.text for entry in app.state.entries))

if __name__ == "__main__": unittest.main()
