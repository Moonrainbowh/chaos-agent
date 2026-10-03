"""Native desktop selection and compact touch mode must coexist."""
import os
import unittest
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_layout import LayoutMode
from code_agent.interfaces.terminal_io import MOUSE_REPORT_ENABLE, MOUSE_REPORT_DISABLE
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class MouseReportingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_run_never_captures_mouse(self):
        output = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.layout_mode = LayoutMode.WIDE
        with patch('code_agent.interfaces.windows_tui.capture_ctrl_c_as_input', return_value=lambda: None), patch('code_agent.interfaces.windows_tui.read_key', side_effect=['\x03', '\x03']):
            await app.run()
        self.assertFalse(any(MOUSE_REPORT_ENABLE in chunk for chunk in output))
        self.assertIn(MOUSE_REPORT_DISABLE, ''.join(output))

    async def test_compact_run_restores_mouse_on_reader_error(self):
        output = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.layout_mode = LayoutMode.COMPACT
        with patch('code_agent.interfaces.windows_tui.capture_ctrl_c_as_input', return_value=lambda: None), patch('code_agent.interfaces.windows_tui.read_key', side_effect=RuntimeError('reader failed')):
            with self.assertRaisesRegex(RuntimeError, 'reader failed'):
                await app.run()
        combined = ''.join(output)
        self.assertIn(MOUSE_REPORT_ENABLE, combined)
        self.assertGreater(combined.rfind(MOUSE_REPORT_DISABLE), combined.find(MOUSE_REPORT_ENABLE))

    async def test_resize_and_explicit_layout_update_capture_once(self):
        output = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.running = True
        app._mouse_reporting_enabled = False
        for width in (100, 49, 49, 100):
            with patch('code_agent.interfaces.tui_presentation.shutil.get_terminal_size', return_value=os.terminal_size((width, 30))):
                app.redraw()
        self.assertEqual(''.join(output).count(MOUSE_REPORT_ENABLE), 1)
        self.assertEqual(''.join(output).count(MOUSE_REPORT_DISABLE), 1)
        app.layout_mode = LayoutMode.COMPACT
        with patch('code_agent.interfaces.tui_presentation.shutil.get_terminal_size', return_value=os.terminal_size((100, 30))):
            app.redraw()
            app.layout_mode = LayoutMode.WIDE
            app.redraw()
        self.assertEqual(''.join(output).count(MOUSE_REPORT_ENABLE), 2)
        self.assertEqual(''.join(output).count(MOUSE_REPORT_DISABLE), 2)
