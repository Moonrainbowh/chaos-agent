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
