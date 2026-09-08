from __future__ import annotations

import asyncio
import re
import unittest
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_renderer import ColorMode, Theme
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def plain(value: str) -> str:
    return ANSI.sub("", value).replace("\r", "")


class CollapsedComposerTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self) -> WindowsTerminalApp:
        writes: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=writes.append,
        )
        app.theme = Theme.SLATE
        return app

    def test_collapsed_frame_is_compact_single_line_capsule(self) -> None:
        frame = render_live_tail_frame(
            "", "ready", 80, theme=Theme.SLATE,
            color=ColorMode.NEVER, expanded=False,
        )
        text = plain(frame.text)
        self.assertEqual(frame.geometry.height, 1)
        self.assertIn("Space", text)
        self.assertIn("ready", text)

    def test_expanded_frame_has_six_line_editor_space(self) -> None:
        frame = render_live_tail_frame(
            "", "ready", 80, terminal_height=30,
            theme=Theme.SLATE, color=ColorMode.NEVER, expanded=True,
        )
        self.assertGreaterEqual(frame.geometry.height, 8)
        text = plain(frame.text)
        self.assertIn("6-ROW", text)
        self.assertIn("Esc collapse", text)

    async def test_space_expands_and_esc_collapses_preserving_draft(self) -> None:
        app = self.make_app()
        app.composer_expanded = False

        # 1. Press space to expand
        await app.handle_key(" ")
        self.assertTrue(app.composer_expanded)
        self.assertEqual(app.input.text, "")

        # 2. Type text in expanded state
        for ch in "hello world":
            await app.handle_key(ch)
        self.assertEqual(app.input.text, "hello world")

        # 3. Press Esc to collapse - draft must be preserved
        await app.handle_key("\x1b")
        self.assertFalse(app.composer_expanded)
        self.assertEqual(app.input.text, "hello world")

        # 4. Press space again - expands with draft intact
        await app.handle_key(" ")
        self.assertTrue(app.composer_expanded)
        self.assertEqual(app.input.text, "hello world")

    async def test_mode_2_printable_character_auto_expands(self) -> None:
        app = self.make_app()
        app.composer_expanded = False

        # Typing a character while collapsed must auto-expand and insert
        await app.handle_key("x")
        self.assertTrue(app.composer_expanded)
        self.assertEqual(app.input.text, "x")

    def test_scrolling_clamps_visible_rows_to_six(self) -> None:
        lines_text = "\n".join(f"line {i}" for i in range(12))
        frame = render_live_tail_frame(
            lines_text, "ready", 80, terminal_height=30,
            theme=Theme.SLATE, color=ColorMode.NEVER, expanded=True,
            cursor_index=len(lines_text),
        )
        text = plain(frame.text)
        # Last line should be visible since cursor is at the end
        self.assertIn("line 11", text)
