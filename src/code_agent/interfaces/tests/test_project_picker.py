from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.project_picker import ProjectPicker
from code_agent.interfaces.terminal_display import display_width
from code_agent.interfaces.terminal_mouse import MouseClick
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_projects import open_project_picker
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.project_launcher.store import ProjectStore


class ProjectPickerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.first, self.second = self.base / "工程一", self.base / "工程二"
        self.first.mkdir(); self.second.mkdir()
        self.store = ProjectStore(self.base / "state" / "projects.json")
        self.store.add(self.first); self.store.add(self.second)

    async def test_startup_mouse_capture_follows_width_and_restores(self):
        from code_agent.interfaces.project_launcher_view import choose_project
        from code_agent.interfaces.terminal_io import MOUSE_REPORT_ENABLE, MOUSE_REPORT_DISABLE
        for width in (49, 100):
            output = []
            with self.subTest(width=width), patch('code_agent.interfaces.project_launcher_view.capture_ctrl_c_as_input', return_value=lambda: None), patch('code_agent.interfaces.project_launcher_view.terminal_size', return_value=os.terminal_size((width, 30))):
                result = await choose_project(self.store, write=output.append, read=lambda **kwargs: '\r')
                self.assertTrue(result.is_dir())
                self.assertEqual(MOUSE_REPORT_ENABLE in ''.join(output), width <= 64)
                self.assertIn(MOUSE_REPORT_DISABLE, output[-1])

    async def test_reopened_picker_retains_recent_selected_project(self):
        picker = ProjectPicker(self.store)
        await picker.load()
        await picker.handle_key("\r")
        reopened = ProjectStore(self.store.path)
        self.assertEqual(reopened.last_root(), picker.result)
        self.assertTrue(picker.result.is_dir())

    async def test_switch_confirmation_cancel_preserves_draft_and_recent(self):
        self.store.select(self.first)
        picker = ProjectPicker(self.store, current_root=self.first, has_draft=True)
        await picker.load()
        picker.selected = next(i for i, item in enumerate(picker.matches) if item.root == self.second)
        await picker.handle_key("\r")
        self.assertEqual(picker.mode, "confirm")
        await picker.handle_key("\r")
        self.assertIsNone(picker.result)
        self.assertFalse(picker.finished)
        self.assertEqual(self.store.last_root(), self.first)
        self.assertEqual(picker.mode, "projects")

    async def test_switch_confirmation_requires_explicit_yes(self):
        picker = ProjectPicker(self.store, current_root=self.first, has_draft=True)
        await picker.load()
        picker.selected = next(i for i, item in enumerate(picker.matches) if item.root == self.second)
        await picker.action("enter")
        await picker.action("item:1")
        self.assertEqual(picker.result, self.second)

    async def test_remove_requires_confirmation_and_leaves_files(self):
        marker = self.first / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        picker = ProjectPicker(self.store)
        await picker.load()
        picker.selected = next(i for i, item in enumerate(picker.matches) if item.root == self.first)
        await picker.action("remove")
        await picker.action("item:1")
        self.assertTrue(marker.exists())
        self.assertNotIn(self.first, [item.root for item in self.store.entries()])

    async def test_browse_explicit_directory_adds_without_cd(self):
        new = self.base / "新项目"
        new.mkdir()
        picker = ProjectPicker(self.store)
        picker.mode, picker.directory = "browse", self.base
        await picker.load()
        picker.selected = next(i for i, item in enumerate(picker.matches) if item.root == new)
        await picker.action("enter")
        await picker.action("use")
        self.assertEqual(picker.result, new)

    async def test_unavailable_project_does_not_finish_or_change_recent(self):
        self.store.select(self.second)
        self.first.rmdir()
        picker = ProjectPicker(self.store)
        await picker.load()
        picker.selected = next(i for i, item in enumerate(picker.matches) if item.root == self.first)
        await picker.action("enter")
        self.assertFalse(picker.finished)
        self.assertIn("不可用", picker.error)
        self.assertEqual(self.store.last_root(), self.second)

    async def test_small_viewport_regions_fit_and_filter_chinese(self):
        picker = ProjectPicker(self.store)
        await picker.load()
        await picker.handle_key("二")
        self.assertEqual([item.root for item in picker.matches], [self.second])
        for width, height in ((7, 4), (24, 7), (49, 20)):
            frame = picker.frame(width, height)
            self.assertLessEqual(frame.geometry.height, height)
            self.assertTrue(all(value <= width for value in frame.geometry.row_widths))
            self.assertTrue(all(region.row < height and region.right <= width for region in frame.touch_regions))

    async def test_overlay_cancellation_preserves_original_multiline_input(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda text: None, workspace_root=self.first)
        app.project_store = self.store
        app.input.insert("保留\n完整草稿")
        original = app.input
        self.assertTrue(await open_project_picker(app))
        await app.handle_key("\x1b")
        self.assertIs(app.input, original)
        self.assertEqual(app.input.text, "保留\n完整草稿")
        self.assertIsNone(getattr(app, "requested_project", None))

    async def test_overlay_touch_resize_rejects_old_selection_then_requests_new_host(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda text: None, workspace_root=self.first)
        app.project_store = self.store
        size = os.terminal_size((49, 20))
        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size", return_value=size):
            await open_project_picker(app)
            picker = app._project_picker
            index = next(i for i, item in enumerate(picker.matches) if item.root == self.second)
            area = next(item for item in app._project_frame.touch_regions if item.action == f"item:{index}")
            click = MouseClick(area.left + 1, area.row + 1)
            with patch("code_agent.interfaces.tui_projects.terminal_size", return_value=os.terminal_size((50, 20))):
                await app.handle_key(click)
            self.assertIsNone(picker.result)
            with patch("code_agent.interfaces.tui_projects.terminal_size", return_value=size):
                await app.handle_key(click)
            self.assertEqual(app.requested_project, self.second)
            self.assertFalse(app.running)

    async def test_running_task_cannot_open_project_overlay(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda text: None)
        app.project_store = self.store
        app._run_task = SimpleNamespace(done=lambda: False)
        self.assertFalse(await open_project_picker(app))
        self.assertIsNone(getattr(app, "_project_picker", None))

    async def test_new_auth_prompt_prevents_old_project_mouse_selection(self):
        import asyncio
        from code_agent.interfaces.tui_auth_prompt import AuthPrompt
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda text: None, workspace_root=self.first)
        app.project_store = self.store
        size = os.terminal_size((49, 20))
        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size", return_value=size):
            await open_project_picker(app)
            picker = app._project_picker
            index = next(i for i, item in enumerate(picker.matches) if item.root == self.second)
            area = next(item for item in app._project_frame.touch_regions if item.action == f"item:{index}")
            prompt = AuthPrompt("API key", asyncio.get_running_loop().create_future())
            app._auth_prompt = prompt
            app.redraw()
            with patch("code_agent.interfaces.tui_projects.terminal_size", return_value=size), patch("code_agent.interfaces.tui_mobile.terminal_size", return_value=size):
                await app.handle_key(MouseClick(area.left + 1, area.row + 1))
            self.assertFalse(picker.finished)
            self.assertIsNone(getattr(app, "requested_project", None))
            self.assertFalse(prompt.result.done())
