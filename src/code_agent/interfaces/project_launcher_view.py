"""Provider-free terminal entry for the persistent project directory."""
from __future__ import annotations

import asyncio
import sys

from .project_picker import ProjectPicker
from .terminal_io import (BRACKETED_PASTE_ENABLE, BRACKETED_PASTE_DISABLE,
                          MOUSE_REPORT_ENABLE, MOUSE_REPORT_DISABLE,
                          capture_ctrl_c_as_input, read_key, stdout_write)
from .terminal_mouse import MouseClick
from .terminal_layout import LayoutMode
from .terminal_size import terminal_size
from .terminal_win32_input import WIN32_INPUT_ENABLE, WIN32_INPUT_DISABLE
import os


async def choose_project(store, *, current_root=None, write=stdout_write, read=read_key):
    """Run the same project picker before creating any Agent runtime."""
    picker = ProjectPicker(store, current_root=current_root)
    await picker.load()
    restore = capture_ctrl_c_as_input()
    windows = os.name == "nt"
    previous_size = None
    frame = None
    mouse_enabled = False
    try:
        write(BRACKETED_PASTE_ENABLE + MOUSE_REPORT_DISABLE + (WIN32_INPUT_ENABLE if windows else ""))
        while not picker.finished:
            size = terminal_size((49, 30))
            compact = LayoutMode.AUTO.resolve(size.columns) is LayoutMode.COMPACT
            if compact != mouse_enabled:
                write(MOUSE_REPORT_ENABLE if compact else MOUSE_REPORT_DISABLE)
                mouse_enabled = compact
            if previous_size != tuple(size) or frame is None:
                frame = picker.frame(*size)
                write("\x1b[?25l" + frame.text + "\x1b[?25h")
                previous_size = tuple(size)
            key = await asyncio.to_thread(read, timeout=.1)
            if key is None:
                continue
            if isinstance(key, MouseClick):
                if tuple(terminal_size((49, 30))) != previous_size:
                    continue
                target = next((area for area in frame.touch_regions if area.contains(key.row - 1, key.column - 1)), None)
                if target:
                    await picker.action(target.action)
            else:
                try:
                    await picker.handle_key(key)
                except ValueError as error:
                    picker.error = str(error)
            frame = None
        return picker.result
    finally:
        write(BRACKETED_PASTE_DISABLE + MOUSE_REPORT_DISABLE + (WIN32_INPUT_DISABLE if windows else ""))
        restore()


async def noninteractive_projects(store):
    """Return a simple directory report for CLI diagnostics without a model."""
    entries = await asyncio.to_thread(store.entries)
    for entry in entries:
        sys.stdout.write(f"{entry.name}\t{entry.root}\t{'available' if entry.available else 'unavailable'}\n")
