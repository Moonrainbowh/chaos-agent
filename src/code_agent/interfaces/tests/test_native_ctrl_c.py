"""Exercise real Windows console records in an isolated hidden console."""
import os
import subprocess
import sys
import unittest


@unittest.skipUnless(os.name == "nt", "Windows console required")
class NativeCtrlCTests(unittest.TestCase):
    def test_failed_composer_exits_on_two_native_ctrl_c_events(self):
        program = r'''
import asyncio
import ctypes
from ctypes import wintypes
from code_agent.interfaces.console_shortcuts import _InputRecord
from code_agent.interfaces.terminal_io import capture_ctrl_c_as_input, read_key
from code_agent.interfaces.tests.test_image_atom_editing import ImageAtomEditingTests

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetStdHandle.restype = wintypes.HANDLE
handle = kernel.GetStdHandle(-10)
kernel.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_InputRecord), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
restore = capture_ctrl_c_as_input()
async def check():
    app, _, _, _ = ImageAtomEditingTests().make_app()
    app.state.status = "failed"
    app.input.insert(":")
    app.running = True
    for expected_running in (True, False):
        record = _InputRecord()
        record.kind = 1
        record.data.key.down = 1
        record.data.key.repeat = 1
        record.data.key.virtual_key = 67
        record.data.key.character = "\x03"
        record.data.key.modifiers = 8
        written = wintypes.DWORD()
        assert kernel.WriteConsoleInputW(handle, ctypes.byref(record), 1, ctypes.byref(written))
        key = await asyncio.to_thread(read_key, timeout=.1)
        assert key == "\x03", repr(key)
        await app.handle_key(key)
        assert app.running is expected_running
    assert app.input.text == ""
try:
    asyncio.run(check())
finally:
    restore()
'''
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        result = subprocess.run(
            [sys.executable, "-c", program], startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
