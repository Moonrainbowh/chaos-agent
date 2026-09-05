from __future__ import annotations

import asyncio
import os
import re
import unittest
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.i18n import Language
from code_agent.interfaces.terminal_display import DisplayKind, display_width, text_entry
from code_agent.interfaces.terminal_motion import TailMotion, motion_allowed, watch_visuals
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entry
from code_agent.interfaces.terminal_status import status_context, status_presentation
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_theme import DESIGNS, preferred_theme
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def plain(value):
    return ANSI.sub("", value).replace("\r", "")


class DesignedTailTests(unittest.TestCase):
    def test_resize_never_overflows_and_always_keeps_cursor_inside_tail(self):
        for theme in DESIGNS:
            for width in (8, 12, 24, 40, 80, 160):
                for height in (4, 5, 8, 24):
                    with self.subTest(theme=theme, width=width, height=height):
                        frame = render_live_tail_frame(
                            "中文路径/" * 30 + "\nlast", "等待审批", width,
                            terminal_height=height, color=ColorMode.NEVER,
                            theme=theme, cursor_index=10, assistant_draft="## 标题\n正文\n" * 20,
                            palette=("› selected command " + "x" * 80,) * 10,
                            status_context="model · task 3k tokens · 00:01",
                        )
                        self.assertLessEqual(frame.geometry.height, height)
                        self.assertLess(frame.geometry.cursor_row, frame.geometry.height)
                        self.assertTrue(all(display_width(row) <= width - 1 for row in plain(frame.text).splitlines()))

    def test_styles_never_accept_terminal_controls_from_untrusted_fields(self):
        for theme in DESIGNS:
            frame = render_live_tail_frame(
                "input\x1b[2J", "status\x1b]0;fake\x07", 80, theme=theme,
                palette=("› choice\x1b[31m",), assistant_draft="**text**\x1b[2J",
                status_context="model\x1b[2J", color=ColorMode.ALWAYS,
            )
            self.assertNotIn("\x1b[2J", frame.text)
            self.assertNotIn("\x1b]0;", frame.text)
            self.assertNotIn("\x1b[31m", frame.text)

    def test_motion_changes_only_style_and_not_cursor_or_content(self):
        for theme in DESIGNS:
            frames = [render_live_tail_frame("hello 中", "ready", 90, theme=theme,
                      color=ColorMode.ALWAYS, motion_progress=t) for t in (0, .5, 1)]
            self.assertEqual(len({item.geometry for item in frames}), 1)
            self.assertEqual(len({plain(item.text) for item in frames}), 1)
            self.assertNotEqual(frames[0].text, frames[-1].text)

    def test_each_theme_applies_to_final_markdown_and_preserves_errors(self):
        for theme, design in DESIGNS.items():
            result = render_entry(text_entry(DisplayKind.AGENT, "## 结论\n**重点**与 `a.py`"),
                                  80, theme=theme, color=ColorMode.ALWAYS)
            self.assertIn("\x1b[1;" + design.accent + "m", result)
            self.assertNotIn("**", plain(result))
            self.assertNotIn("`", plain(result))
            failed = status_presentation("failed", "", None, Language.EN_US, theme, 2)
            ready = status_presentation("idle", "", None, Language.EN_US, theme, 2)
            self.assertNotEqual(failed[2], ready[2])
            self.assertNotEqual(ready[2], status_presentation("completed", "", None, Language.EN_US, theme, 2)[2])

    def test_usage_is_labeled_as_task_total_without_an_invented_capacity(self):
        result = status_context("model", 0, 3, tokens=3010, show_percentage=False)
        self.assertIn("task 3.0k tokens", result)
        self.assertNotIn("%", result)

    def test_motion_clock_settles_and_does_not_restart_on_unchanged_key(self):
        motion = TailMotion()
        motion.observe("ready", 10)
        self.assertEqual(motion.progress(10), 0)
        motion.observe("ready", 10.1)
        self.assertFalse(motion.active(10.4))
        self.assertEqual(motion.progress(10.4), 1)
        motion.observe("running", 11)
        self.assertTrue(motion.active(11.1))

    def test_env_theme_has_safe_fallback(self):
        self.assertEqual(preferred_theme({"CHAOS_THEME": "EMBER"}), Theme.EMBER)
        self.assertEqual(preferred_theme({"CHAOS_THEME": "bad"}), Theme.AURORA)


class ThemeInteractionTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        return WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)

    async def test_theme_commands_reach_the_real_app_without_starting_a_model(self):
        app = self.make_app()
        for theme in DESIGNS:
            self.assertTrue(await app.submit(":theme " + theme.value))
            self.assertEqual(app.theme, theme)
            self.assertIsNone(app._run_task)
        await app.submit(":theme motion off")
        self.assertFalse(app.motion.enabled)
        before = app.theme
        self.assertFalse(await app.submit(":theme invalid"))
        self.assertEqual(app.theme, before)

    async def test_no_color_and_reduced_motion_have_static_feedback(self):
        app = self.make_app()
        app.theme = Theme.AURORA
        for environment in ({"NO_COLOR": "1"}, {"CHAOS_REDUCED_MOTION": "1"}):
            with patch.dict(os.environ, environment, clear=True):
                self.assertFalse(motion_allowed(app))
        app.color = ColorMode.NEVER
        self.assertFalse(motion_allowed(app))

    async def test_idle_visual_watcher_does_not_write_unchanged_frames(self):
        app = self.make_app()
        app.theme = Theme.MONO
        app.running = True
        app.motion.enabled = False
        app.redraw()
        writes = []
        app._write = writes.append
        watcher = asyncio.create_task(watch_visuals(app))
        await asyncio.sleep(.12)
        app.running = False
        await watcher
        self.assertEqual(writes, [])
