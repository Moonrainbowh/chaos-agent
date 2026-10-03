from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, render_entries
from code_agent.interfaces.terminal_size import TerminalSize
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_theme import Theme
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class ScrolledViewCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None,
        )
        self.app.color = ColorMode.NEVER
        self.app.state.entries = [
            text_entry(DisplayKind.AGENT, f"history {index}") for index in range(30)
        ]
        self.frame = SimpleNamespace(geometry=SimpleNamespace(height=3), text="TAIL")
        self.size = TerminalSize(80, 20)

    def test_repeated_scroll_reuses_formatted_history(self) -> None:
        with patch("code_agent.interfaces.tui_presentation.render_entries", wraps=render_entries) as render:
            self.app.scroll_viewport(1)
            first = self.app._render_scrolled_view(self.frame, self.size)
            self.app.scroll_viewport(1)
            second = self.app._render_scrolled_view(self.frame, self.size)

        self.assertNotEqual(first, second)
        self.assertEqual(render.call_count, 1)

    def test_append_formats_only_new_entries_and_resize_reformats_history(self) -> None:
        with patch("code_agent.interfaces.tui_presentation.render_entries", wraps=render_entries) as render:
            self.app._render_scrolled_view(self.frame, self.size)
            self.app.state.entries.append(text_entry(DisplayKind.AGENT, "new answer"))
            self.app.scroll_to_bottom()
            appended = self.app._render_scrolled_view(self.frame, self.size)
            self.assertEqual(len(render.call_args.args[0]), 1)
            self.assertIn("new answer", appended)

            narrower = self.app._render_scrolled_view(self.frame, TerminalSize(40, 20))
            self.assertEqual(len(render.call_args.args[0]), 31)
            self.assertIn("new answer", narrower)

    def test_replaced_history_does_not_show_cached_rows(self) -> None:
        self.app._render_scrolled_view(self.frame, self.size)
        self.app.state.entries = [text_entry(DisplayKind.AGENT, "other session")]
        result = self.app._render_scrolled_view(self.frame, self.size)
        self.assertIn("other session", result)
        self.assertNotIn("history 29", result)

    def test_incremental_cache_matches_full_layout_across_entry_gaps(self) -> None:
        self.app.state.entries = [text_entry(DisplayKind.USER, "question")]
        self.app._render_scrolled_view(self.frame, self.size)
        self.app.state.entries.extend([
            text_entry(DisplayKind.AGENT, "## Answer\nline one"),
            text_entry(DisplayKind.TOOL, "tool result"),
            text_entry(DisplayKind.AGENT, "final answer"),
        ])
        self.app._render_scrolled_view(self.frame, self.size)
        expected = render_entries(
            self.app.state.entries, self.size.columns,
            theme=self.app.theme, color=self.app.color,
        ).splitlines()
        self.assertEqual(self.app._history_cache_lines, expected)

    def test_theme_change_reformats_cached_history(self) -> None:
        self.app._render_scrolled_view(self.frame, self.size)
        self.app.theme = Theme.PLAIN
        with patch("code_agent.interfaces.tui_presentation.render_entries", wraps=render_entries) as render:
            self.app._render_scrolled_view(self.frame, self.size)
        self.assertEqual(render.call_count, 1)
        self.assertEqual(len(render.call_args.args[0]), 30)

    def test_regular_redraw_warms_cache_before_first_wheel_event(self) -> None:
        with patch("code_agent.interfaces.tui_presentation._presentation_terminal_size", return_value=self.size):
            with patch("code_agent.interfaces.tui_presentation.render_entries", wraps=render_entries) as render:
                self.app.redraw()
                self.app.scroll_viewport(1)
                self.app.redraw()
        self.assertEqual(render.call_count, 1)
