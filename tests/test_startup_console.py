"""Native Windows acceptance for pre-launch clearing and TUI handoff."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_PROBE = r'''
import asyncio, ctypes, msvcrt, os, sys, traceback
from ctypes import wintypes
from pathlib import Path

def check():
    from code_agent.interfaces.startup_splash import StartupSplash, _enable_vt
    sys.stdout = open("CONOUT$", "w", encoding="utf-8")
    sys.stdin = open("CONIN$", "r")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class Coord(ctypes.Structure):
        _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]
    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_short), ("top", ctypes.c_short), ("right", ctypes.c_short), ("bottom", ctypes.c_short)]
    class Info(ctypes.Structure):
        _fields_ = [("size", Coord), ("cursor", Coord), ("attributes", wintypes.WORD), ("window", Rect), ("max_size", Coord)]
    kernel.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
    kernel.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, Coord, ctypes.POINTER(wintypes.DWORD)]
    def screen():
        with open("CONOUT$", "w") as current:
            handle = msvcrt.get_osfhandle(current.fileno())
            info = Info()
            assert kernel.GetConsoleScreenBufferInfo(handle, ctypes.byref(info))
            text = ctypes.create_unicode_buffer(info.size.x * (info.window.bottom-info.window.top+1))
            read = wintypes.DWORD()
            assert kernel.ReadConsoleOutputCharacterW(handle, text, len(text), Coord(0,info.window.top), ctypes.byref(read))
            return text[:read.value]
    restore = _enable_vt(sys.stdout)
    try:
        sys.stdout.write("\x1b[2J\x1b[HORIGINAL HISTORY\n")
        sys.stdout.flush()
        os.environ["CHAOS_REDUCED_MOTION"] = "1"
        splash = StartupSplash()
        try:
            splash.start()
            splash._draw(.5)
            half = screen()
            assert "ORIGINAL HISTORY" not in half
            splash._draw(1)
            whole = screen()
            assert whole.count("█") > half.count("█") > 0, (whole.count("█"),half.count("█"))
        finally:
            splash.stop()
        assert "ORIGINAL HISTORY" not in screen()
        assert "█" not in screen()
        sys.stdout.write("RUNTIME HISTORY\n")
        sys.stdout.flush()
        from types import SimpleNamespace
        from code_agent.interfaces.windows_tui import WindowsTerminalApp
        from code_agent.interfaces.approval import ApprovalBroker
        app = WindowsTerminalApp(SimpleNamespace(), ApprovalBroker())
        redraw = app.redraw
        def first_frame():
            redraw()
            text = screen()
            assert "›" in text, repr(text)
            assert "ORIGINAL HISTORY" not in text
            assert "RUNTIME HISTORY" in text
            app.running = False
        app.redraw = first_frame
        asyncio.run(app.run())
    finally:
        restore()
try:
    check()
except BaseException:
    Path(sys.argv[1]).write_text(traceback.format_exc(), encoding="utf-8")
    sys.exit(1)
'''


@unittest.skipUnless(os.name == "nt", "Windows console required")
class StartupConsoleTests(unittest.TestCase):
    def test_scan_clears_prelaunch_history_and_preserves_runtime_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            errors = Path(temporary) / "errors.txt"
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            result = subprocess.run(
                [sys.executable, "-c", _PROBE, str(errors)],
                startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE,
                timeout=20,
            )
            detail = errors.read_text(encoding="utf-8") if errors.exists() else ""
            self.assertEqual(result.returncode, 0, detail)
