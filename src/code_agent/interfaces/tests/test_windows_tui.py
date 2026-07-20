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
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.terminal_status import status_presentation
from code_agent.interfaces.terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class InputBufferTests(unittest.TestCase):
    def test_editing_history_and_clear_shortcut_state(self) -> None:
        buffer = InputBuffer()
        buffer.insert("abc"); buffer.move_left(); buffer.insert("X")
        self.assertEqual(buffer.text, "abXc")
        self.assertEqual(buffer.submit(), "abXc")
        buffer.previous(); self.assertEqual(buffer.text, "abXc")
        buffer.clear(); self.assertEqual(buffer.text, "")

    def test_line_break_and_vertical_cursor_movement(self) -> None:
        buffer = InputBuffer()
        buffer.insert("one"); buffer.insert_line_break(); buffer.insert("xy")

        self.assertEqual(buffer.text, "one\nxy")
        self.assertTrue(buffer.move_up()); self.assertEqual(buffer.cursor, 2)
        self.assertTrue(buffer.move_down()); self.assertEqual(buffer.cursor, 6)
        buffer.move_home(); self.assertEqual(buffer.cursor, 4)
        buffer.move_end(); self.assertEqual(buffer.cursor, 6)

    def test_paste_normalizes_newlines_and_is_bounded(self) -> None:
        self.assertEqual(paste_event("第一行\r\n第二行\r第三行").value, "第一行\n第二行\n第三行")
        with self.assertRaises(ValueError):
            paste_event("x" * (MAX_PASTE_BYTES + 1))

    def test_exit_guard_requires_second_interrupt_and_disarms_on_input(self) -> None:
        clock = [0.0]
        guard = ExitGuard(clock=lambda: clock[0])
        self.assertFalse(guard.interrupt())
        clock[0] = 1.9; self.assertTrue(guard.interrupt())
        self.assertFalse(guard.interrupt())
        guard.input_received(); clock[0] = 2.0
        self.assertFalse(guard.interrupt())

    def test_bracketed_paste_terminal_modes_are_explicit(self) -> None:
        self.assertEqual(BRACKETED_PASTE_ENABLE, "\x1b[?2004h")
        self.assertEqual(BRACKETED_PASTE_DISABLE, "\x1b[?2004l")


class WindowsTerminalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_palette_executes_leaf_and_lists_modes_at_the_root(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.input.replace("/状态")

        await app.handle_key("\r")

        self.assertEqual(app.input.text, "")
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.METADATA)

        app.modes = type("Modes", (), {"current": type("Mode", (), {"model": "test-model"})()})()
        app.input.replace("/模式")
        rows = app.interactions.rows(app)
        self.assertTrue(any("/模式 low" in row for row in rows))
        self.assertTrue(any("/模式 medium" in row for row in rows))
        self.assertTrue(any("/模式 high" in row for row in rows))
        self.assertTrue(any("/模式 ultra" in row for row in rows))

    async def test_help_is_grouped_multiline_and_omits_removed_commands(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)

        self.assertTrue(await app.submit("/帮助"))

        help_text = app.state.entries[-1].text
        self.assertIn("通用\n", help_text)
        self.assertIn("/帮助", help_text)
        self.assertIn("/状态", help_text)
        self.assertNotIn("/颜色", help_text)
        self.assertNotIn("/模型", help_text)

    async def test_mode_prefix_selects_a_flat_mode_choice_with_one_enter(self) -> None:
        class Modes:
            current = type("Mode", (), {"name": "medium", "model": "test-model"})()

            async def use(self, name: str, *, idle: bool):
                self.seen = (name, idle)
                self.current = type("Mode", (), {"name": name, "model": "selected-model"})()
                return self.current

        modes = Modes()
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.modes = modes
        app.input.replace("/模式")

        await app.handle_key("\r")

        self.assertEqual(modes.seen, ("low", True))
        self.assertEqual(app.input.text, "")

    async def test_enter_submits_complete_restore_command_without_picker_replacement(self) -> None:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), history=object(), write=lambda _: None,
        )
        seen = []

        async def restore(thread_id: str) -> bool:
            seen.append(thread_id)
            return True

        app.restore_thread = restore
        app.input.replace("/恢复 T-042")

        await app.handle_key("\r")

        self.assertEqual(seen, ["T-042"])
        self.assertEqual(app.input.text, "")

    async def test_enter_switches_mode_and_updates_status_model(self) -> None:
        class Modes:
            current = type("Mode", (), {"name": "medium", "model": "old-model"})()

            async def use(self, name: str, *, idle: bool):
                self.seen = (name, idle)
                self.current = type("Mode", (), {"name": name, "model": "new-model"})()
                return self.current

        modes = Modes()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), modes=modes, write=lambda _: None,
        )
        app.input.replace("/模式 high")

        await app.handle_key("\r")

        self.assertEqual(modes.seen, ("high", True))
        self.assertIn("mode selected: high · new-model", app.state.entries[-1].text)
        self.assertEqual(app.input.text, "")

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
        self.assertEqual(plain.count("◆ full answer"), 1)
        self.assertNotIn("◆ full \n", plain)

    async def test_raw_reasoning_is_not_written(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.REASONING_DELTA, text="private work").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {}),
        )
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=lambda _: None)
        await app.submit("inspect"); await app.wait_idle()
        self.assertNotIn("private work", "\n".join(entry.text for entry in app.state.entries))

    async def test_new_prompt_clears_the_old_status_before_appending_it(self) -> None:
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.state.status = "completed"; app.state.execution_summary = "已完成 3 项操作"
        app.redraw()

        await app.submit("修改成乘法函数吧")

        appended_prompt = _plain(output[1])
        self.assertIn("› 修改成乘法函数吧", appended_prompt)
        self.assertNotIn("已完成 3 项操作", appended_prompt)
        self.assertNotIn("\x1b[1B", appended_prompt)
        await app.wait_idle()

    async def test_running_icon_changes_but_completion_icon_is_static(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.state.begin_run(); first = status_presentation(app.state.status, app.state.execution_summary, app.state.active_action, app.catalog.language, app.theme, app._spinner_index)
        app._spinner_index = 1; second = status_presentation(app.state.status, app.state.execution_summary, app.state.active_action, app.catalog.language, app.theme, app._spinner_index)
        app.state.status = "completed"; app.state.execution_summary = "已完成 1 项操作"

        self.assertNotEqual(first[1], second[1])
        completed = status_presentation(app.state.status, app.state.execution_summary, app.state.active_action, app.catalog.language, app.theme, app._spinner_index)
        self.assertEqual(completed[1], "✓")

    async def test_arrow_keys_and_ctrl_u_edit_instead_of_printing_escape_bytes(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        for key in ("a", "b", "left", "X"):
            await app.handle_key(key)
        self.assertEqual(app.input.text, "aXb")
        await app.handle_key("\x15")
        self.assertEqual(app.input.text, "")

    async def test_ctrl_j_inserts_a_line_break_and_enter_submits_multiline_text(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        for key in ("first", "\n", "second"):
            await app.handle_key(key)

        self.assertEqual(app.input.text, "first\nsecond")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "")
        self.assertEqual(app.state.entries[0].text, "first\nsecond")
        await app.wait_idle()

    async def test_bracketed_paste_inserts_once_without_submitting(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        await app.handle_key("\x1b[200~/状态\r\n第二行\x1b[201~")

        self.assertEqual(app.input.text, "/状态\n第二行")
        self.assertEqual(app.state.entries, [])

    async def test_malformed_slash_command_is_in_band_error(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        self.assertFalse(await app.submit("/does-not-exist"))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)

    async def test_pending_approval_rejects_escape_and_locks_new_submission(self) -> None:
        broker = ApprovalBroker()
        app = WindowsTerminalApp(AgentController(FakeEngine(())), broker, write=lambda _: None)
        request = ApprovalRequest("approval-1", "run_command", {"command": "Get-Date"})
        pending = asyncio.create_task(broker.request(request, CancellationToken()))
        app._pending_approval = await broker.next_request()

        self.assertFalse(await app.submit("second request"))
        await app.handle_key("\x1b")

        self.assertFalse(await pending)
        self.assertIsNone(app._pending_approval)


if __name__ == "__main__": unittest.main()
