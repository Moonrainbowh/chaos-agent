"""Dependency-free startup feedback while the application imports and prepares."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time

from .startup_logo import scan_frame


class StartupSplash:
    """Own an alternate screen, then clear pre-launch output before the TUI."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self._done = threading.Event()
        self._thread = None
        self._visible = False
        self._restore = lambda: None
        self._size = None
        self._last_frame = None

    def start(self):
        if not self.stream.isatty():
            return
        self._restore = _enable_vt(self.stream)
        self._visible = True
        self._write("\x1b[?1049h\x1b[?25l\x1b[2J")
        self._color = "NO_COLOR" not in os.environ
        static = ("NO_COLOR" in os.environ or
                  os.environ.get("CHAOS_REDUCED_MOTION", "").lower() in {"1", "true", "on"})
        self._draw(1.0 if static else 0.025)
        if not static:
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()

    def _write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def _draw(self, progress):
        try:
            size = os.get_terminal_size(self.stream.fileno())
        except (OSError, ValueError):
            size = shutil.get_terminal_size((100, 30))
        frame = scan_frame(size.columns, size.lines, progress, color=self._color)
        if size != self._size:
            self._write("\x1b[2J")
            self._size = size
            self._last_frame = None
        if frame != self._last_frame:
            self._write(frame)
            self._last_frame = frame

    def _animate(self):
        started = time.monotonic()
        while not self._done.wait(1 / 30):
            elapsed = time.monotonic() - started
            self._draw(min(1.0, elapsed / 1.2))
            if elapsed >= 1.2:
                while not self._done.wait(.1):
                    self._draw(1.0)
                return

    def stop(self):
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        if self._visible:
            try:
                # Clear the main buffer only after leaving the animation screen.
                self._write("\x1b[0m\x1b[?1049l\x1b[2J\x1b[3J\x1b[H\x1b[?25h")
            finally:
                self._visible = False
                self._restore()


def _enable_vt(stream):
    if os.name != "nt":
        return lambda: None
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = msvcrt.get_osfhandle(stream.fileno())
    original = wintypes.DWORD()
    if kernel.GetConsoleMode(handle, ctypes.byref(original)):
        kernel.SetConsoleMode(handle, original.value | 4)
        return lambda: kernel.SetConsoleMode(handle, original.value)
    return lambda: None
