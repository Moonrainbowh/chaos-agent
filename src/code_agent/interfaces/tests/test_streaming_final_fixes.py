from __future__ import annotations

import asyncio
import re
import unittest

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, display_width
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_lifecycle import close_tasks
from code_agent.interfaces.windows_tui import WindowsTerminalApp


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _delta(text: str) -> AgentEvent:
    model_event = ModelEvent(ModelEventKind.TEXT_DELTA, text=text)
    return AgentEvent(EventKind.MODEL_EVENT, {"event": model_event.to_dict()})


class StreamingStateBoundaryTests(unittest.TestCase):
    def test_cancelled_and_error_partial_answers_have_a_local_bound(self) -> None:
        for terminal in (EventKind.CANCELLED, EventKind.ERROR):
            with self.subTest(terminal=terminal):
                state = TerminalState()
                state.apply(_delta("内容" * 20_000))
                state.apply(AgentEvent(terminal, {}))

                partial = state.entries[-1]
                self.assertIs(partial.kind, DisplayKind.PARTIAL_AGENT)
                self.assertLessEqual(len(partial.text), 5_000)
                self.assertTrue(partial.text.endswith("… [本地截断]"))
                self.assertEqual(state.transcript, [])

    def test_successful_final_answer_is_not_truncated(self) -> None:
        answer = "完整" * 10_000
        state = TerminalState()
        state.apply(_delta(answer))
        message = Message(role="assistant", content=answer)
        state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()}))

        self.assertEqual(state.entries[-1].text, answer)
        self.assertNotIn("[本地截断]", state.entries[-1].text)

    def test_model_started_opens_a_new_safe_draft_revision(self) -> None:
        state = TerminalState()
        state.apply(_delta("old\x1b[2J"))
        old_revision = state.draft_revision
        self.assertEqual(state.draft_answer, "old?[2J")

        state.apply(AgentEvent(EventKind.MODEL_STARTED, {}))
        self.assertFalse(state.has_draft)
        self.assertGreater(state.draft_revision, old_revision)
        state.apply(_delta("e\u0301"))
        self.assertEqual(state.draft_answer, "e\u0301")


class StreamingGeometryTests(unittest.TestCase):
    def test_combining_graphemes_are_never_split(self) -> None:
        frame = render_live_tail_frame(
            "e\u0301e\u0301", "running", 8,
            cursor_index=2,
            assistant_draft="e\u0301e\u0301",
            terminal_height=8,
        )

        self.assertEqual(display_width("e\u0301"), 1)
        self.assertNotIn("e\n\r\u0301", _ANSI.sub("", frame.text))
        self.assertLessEqual(frame.geometry.height, 8)

    def test_ultra_narrow_and_short_terminals_keep_geometry_bounded(self) -> None:
        for width, height in ((1, 1), (2, 2), (4, 3), (7, 4)):
            with self.subTest(width=width, height=height):
                frame = render_live_tail_frame(
                    "very long composer", "running", width,
                    assistant_draft="中" * 20,
                    terminal_height=height,
                    palette=("› " + "picker" * 10,) * 5,
                )
                plain_lines = _ANSI.sub("", frame.text).split("\n\r")
                self.assertLessEqual(frame.geometry.height, height)
                self.assertTrue(all(display_width(line) <= width for line in plain_lines))
                if width == 1:
                    self.assertNotIn("\x1b[1C", frame.text)

    def test_long_composer_and_picker_can_reduce_draft_budget_to_zero(self) -> None:
        frame = render_live_tail_frame(
            "line one\nline two\nline three", "running", 20,
            assistant_draft="draft",
            terminal_height=5,
            palette=("› first", "  second", "  third"),
        )

        self.assertLessEqual(frame.geometry.height, 5)
        self.assertNotIn("正在回答", _ANSI.sub("", frame.text))

    def test_draft_title_is_clipped_to_terminal_width(self) -> None:
        frame = render_live_tail_frame(
            "", "running", 7,
            assistant_draft="x",
            terminal_height=8,
        )
        lines = _ANSI.sub("", frame.text).split("\n\r")
        self.assertNotIn("◆ 正在回答", lines)
        self.assertTrue(all(display_width(line) <= 7 for line in lines))


class _StubbornTasks:
    def __init__(self) -> None:
        self.interrupted = False

    async def interrupt(self, task_id: str, reason: str) -> None:
        self.interrupted = task_id == "task-1" and reason == "TUI closed"


class _CancellingTasks:
    def __init__(self) -> None:
        self.run_task: asyncio.Task[object] | None = None

    async def interrupt(self, task_id: str, reason: str) -> None:
        assert self.run_task is not None
        self.run_task.cancel()


class StreamingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_tasks_accepts_cooperative_run_task_cancellation(self) -> None:
        tasks = _CancellingTasks()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            tasks=tasks, write=lambda _: None,
        )
        app.active_task_id = "task-1"
        app._run_task = asyncio.create_task(asyncio.Event().wait())
        tasks.run_task = app._run_task

        await close_tasks(app)

        self.assertTrue(app._run_task.cancelled())

    async def test_close_tasks_freezes_residual_draft_and_clears_live_tail(self) -> None:
        output: list[str] = []
        tasks = _StubbornTasks()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            tasks=tasks, write=output.append,
        )
        app.active_task_id = "task-1"
        app.state.apply(_delta("unfinished close answer"))
        app.redraw()
        never_finishes = asyncio.Event()
        app._run_task = asyncio.create_task(never_finishes.wait())

        await asyncio.wait_for(close_tasks(app), timeout=1)

        self.assertTrue(tasks.interrupted)
        self.assertIs(app.state.entries[-1].kind, DisplayKind.PARTIAL_AGENT)
        self.assertEqual(app.state.transcript, [])
        self.assertFalse(app.state.has_draft)
        self.assertIsNone(app._tail_geometry)
        plain = _ANSI.sub("", "".join(output))
        self.assertEqual(plain.count("未完成回答"), 1)

    async def test_animation_does_not_rewrite_unchanged_draft_at_30_fps(self) -> None:
        release = asyncio.Event()

        class GatedEngine:
            async def run(self, *_: object, **__: object):
                yield _delta("stable draft")
                await release.wait()
                yield AgentEvent(EventKind.COMPLETED, {})

        output: list[str] = []
        app = WindowsTerminalApp(
            AgentController(GatedEngine()), ApprovalBroker(), write=output.append
        )
        await app.submit("inspect")
        app._next_spinner_at = float("inf")
        await asyncio.sleep(0.06)
        settled_count = len(output)
        await asyncio.sleep(0.02)
        self.assertEqual(len(output), settled_count)
        release.set()
        await app.wait_idle()


if __name__ == "__main__":
    unittest.main()
