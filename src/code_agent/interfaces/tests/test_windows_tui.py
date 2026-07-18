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
from code_agent.interfaces.terminal_status import status_presentation
from code_agent.interfaces.terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class TerminalFirstRendererTests(unittest.TestCase):
    def test_entry_uses_local_ansi_only_and_strips_model_controls(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.ERROR, "bad\x1b[2J\x00"), 80, color=ColorMode.ALWAYS)
        self.assertIn("\x1b[31m", rendered)
        self.assertNotIn("\x1b[2J", rendered)
        self.assertIn("bad?[2J?", rendered)

    def test_never_color_has_ascii_fallback(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.SUCCESS, "saved"), 80, theme=Theme.SIGNAL, color=ColorMode.NEVER)
        self.assertEqual(rendered, "+ saved")
        self.assertNotIn("\x1b", rendered)

    def test_user_agent_and_tool_rows_have_distinct_semantic_colors(self) -> None:
        user = render_entry(text_entry(DisplayKind.USER, "request"), 80, color=ColorMode.ALWAYS)
        agent = render_entry(text_entry(DisplayKind.AGENT, "answer"), 80, color=ColorMode.ALWAYS)
        tool = render_entry(text_entry(DisplayKind.TOOL, "read_file"), 80, color=ColorMode.ALWAYS)

        self.assertIn("38;5;80", user)
        self.assertIn("38;5;80", agent)
        self.assertIn("38;5;250", tool)

    def test_agent_body_is_white_while_its_marker_keeps_semantic_color(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "answer"), 80, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[38;5;80m◆\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;252manswer\x1b[0m", rendered)

    def test_tool_records_are_dim_while_task_success_is_green(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.TOOL, "read_file completed"), 80, color=ColorMode.ALWAYS)
        completed = render_entry(text_entry(DisplayKind.SUCCESS, "任务已完成"), 80, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[38;5;250m↳\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;250mread_file completed\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;114m✓\x1b[0m", completed)
        self.assertIn("\x1b[38;5;114m任务已完成\x1b[0m", completed)

    def test_cjk_clipping_uses_display_columns(self) -> None:
        self.assertEqual(display_width("ab中文"), 6)
        self.assertEqual(clip_display("ab中文", 5), "ab中")

    def test_live_tail_does_not_clear_screen_or_enable_mouse_tracking(self) -> None:
        tail = render_live_tail("next", "idle", 80, color=ColorMode.NEVER)
        self.assertIn("› next", tail)
        self.assertIn(". idle", tail)
        self.assertNotIn("[2J", tail)
        self.assertNotIn("[?100", tail)

    def test_live_tail_returns_cursor_to_the_end_of_the_input(self) -> None:
        tail = render_live_tail("中文", "idle", 80, color=ColorMode.NEVER)
        self.assertTrue(tail.endswith("\x1b[2A\x1b[8C"))

    def test_empty_composer_has_a_bordered_placeholder_with_cursor_after_prompt(self) -> None:
        tail = render_live_tail("", "idle", 80, color=ColorMode.ALWAYS)
        plain = _plain(tail)
        self.assertIn("│ › 输入任务、编辑请求，或输入 / 查看命令", plain)
        self.assertIn("\x1b[38;5;247m", tail)
        self.assertNotIn("\x1b[2;", tail)
        self.assertIn("╭─────────────────────────────────────────────────────────────────────────────╮", plain)
        self.assertIn("╰─────────────────────────────────────────────────────────────────────────────╯", plain)
        self.assertTrue(tail.endswith("\x1b[2A\x1b[4C"))

    def test_multiline_composer_grows_and_tracks_the_active_row(self) -> None:
        frame = render_live_tail_frame("first\nsecond", "idle", 40, color=ColorMode.NEVER)

        self.assertEqual(frame.geometry.height, 5)
        self.assertEqual(frame.geometry.cursor_row, 2)
        self.assertIn("│ › first", frame.text)
        self.assertIn("│   second", frame.text)

    def test_palette_is_rendered_above_the_composer(self) -> None:
        tail = render_live_tail("/", "idle", 80, color=ColorMode.NEVER, palette=("/帮助", "/状态"))
        self.assertEqual(tail.count("\n"), 5)
        plain = _plain(tail).replace("\r", "")
        self.assertLess(plain.index("/帮助"), plain.index("╭"))
        self.assertLess(plain.index("/状态"), plain.index("╭"))
        self.assertNotIn("idle |", plain)

    def test_palette_moves_the_composer_cursor_below_its_rows(self) -> None:
        frame = render_live_tail_frame("/", "idle", 80, color=ColorMode.NEVER, palette=("/帮助", "/状态"))

        self.assertEqual(frame.geometry.cursor_row, 3)

    def test_transcript_uses_medium_spacing_without_expanding_tool_groups(self) -> None:
        rendered = render_entries(
            (
                text_entry(DisplayKind.USER, "first"),
                text_entry(DisplayKind.TOOL, "read_file completed"),
                text_entry(DisplayKind.AGENT, "done"),
                text_entry(DisplayKind.USER, "next"),
            ),
            80,
            theme=Theme.SYMBOL,
            color=ColorMode.NEVER,
        )

        self.assertIn("› first\n↳ read_file completed\n\n◆ done\n\n› next", rendered)

    def test_status_context_is_right_aligned_and_hidden_when_space_is_tight(self) -> None:
        wide = _plain(render_live_tail("x", "处理中", 80, color=ColorMode.ALWAYS, status_context="gpt-5 · 00:18"))
        narrow = _plain(render_live_tail("x", "处理中", 16, color=ColorMode.NEVER, status_context="gpt-5 · 00:18"))

        self.assertIn("gpt-5 · 00:18", wide)
        self.assertNotIn("gpt-5 · 00:18", narrow)

    def test_compatibility_renderer_is_append_only(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker())
        app.state.entries.append(text_entry(DisplayKind.AGENT, "done"))
        rendered = render_terminal(app.state, "", 80, 24)
        self.assertIn("◆ done", rendered)
        self.assertNotIn("[2J", rendered)

    def test_markdown_heading_and_paragraphs_render_on_separate_lines(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "说明\n\n### 功能说明\n\n- 项目"), 80, color=ColorMode.NEVER)
        self.assertIn("◆ 说明", rendered)
        self.assertIn("  功能说明", rendered)
        self.assertIn("  - 项目", rendered)
        self.assertNotIn("###", rendered)

    def test_markdown_table_uses_three_rules_without_vertical_lines(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| 位置 | 原来 | 现在 |\n| --- | --- | --- |\n| 函数名 | add(a, b) | multiply(a, b) |"), 44, color=ColorMode.NEVER)

        self.assertIn("─", rendered)
        self.assertIn("函数名", rendered)
        self.assertNotIn("│", rendered)
        self.assertNotIn("| ---", rendered)
        self.assertTrue(all(display_width(line) <= 44 for line in rendered.splitlines()))

    def test_markdown_table_header_and_border_use_distinct_local_colors(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| A | B |\n| --- | --- |\n| 1 | 2 |"), 40, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[1;96m", rendered)
        self.assertIn("\x1b[38;5;247m", rendered)


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
    async def test_palette_executes_leaf_with_one_enter_and_opens_mode_menu(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.input.replace("/状态")

        await app.handle_key("\r")

        self.assertEqual(app.input.text, "")
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.METADATA)

        app.modes = type("Modes", (), {"current": type("Mode", (), {"model": "test-model"})()})()
        app.input.replace("/模式")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/模式 ")
        self.assertTrue(any("/模式 high" in row for row in app.interactions.rows(app)))

    async def test_help_is_grouped_multiline_and_omits_removed_commands(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)

        self.assertTrue(await app.submit("/帮助"))

        help_text = app.state.entries[-1].text
        self.assertIn("通用\n", help_text)
        self.assertIn("/帮助", help_text)
        self.assertIn("/状态", help_text)
        self.assertNotIn("/颜色", help_text)
        self.assertNotIn("/模型", help_text)

    async def test_exact_mode_command_never_selects_another_command(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.modes = type("Modes", (), {"current": type("Mode", (), {"model": "test-model"})()})()
        app.input.replace("/")
        app.interactions.rows(app)
        app.interactions.picker.move(-1)
        app.input.replace("/模式")

        await app.handle_key("\r")

        self.assertEqual(app.input.text, "/模式 ")

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
