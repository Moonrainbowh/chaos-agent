"""Read actual console cells after resize, including append-before-redraw."""
import os
import unittest

if os.name == "nt":
    from code_agent.interfaces.tests.conpty_support import ConsoleProcess
    from code_agent.interfaces.tests.test_conpty_paste import _program


@unittest.skipUnless(os.name == "nt", "Windows ConPTY required")
class ConPtyResizeTests(unittest.TestCase):
    def test_resize_and_append_leave_one_composer(self):
        body = r'''
import shutil, time
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.terminal_renderer import Theme, ColorMode
class Coord(ctypes.Structure):
    _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short)]
class Rect(ctypes.Structure):
    _fields_ = [(n, ctypes.c_short) for n in ('left','top','right','bottom')]
class Info(ctypes.Structure):
    _fields_ = [('size', Coord), ('cursor', Coord), ('attr', wintypes.WORD), ('window', Rect), ('maximum', Coord)]
out = msvcrt.get_osfhandle(sys.stdout.fileno())
k.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
k.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, Coord, ctypes.POINTER(wintypes.DWORD)]
def write(text):
    sys.stdout.write(text); sys.stdout.flush()
app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=write)
app.theme = Theme.SLATE; app.color = ColorMode.ALWAYS
app.input.insert('draft [image1]')
app._append(DisplayKind.AGENT, 'HISTORY_SENTINEL')
app.redraw()
write('\x1b[?9001l')
for step in range(5):
    write(f'\x1b]0;READY_{step}\x07')
    expected = (160, 60, 140, 70, 100)[step]
    deadline = time.monotonic() + 5
    while shutil.get_terminal_size().columns != expected:
        assert time.monotonic() < deadline, shutil.get_terminal_size()
        time.sleep(.01)
    if step % 2:
        app._append(DisplayKind.METADATA, 'clipboard images staged')
    app.redraw()
    info = Info(); assert k.GetConsoleScreenBufferInfo(out, ctypes.byref(info))
    count = info.size.x * info.size.y
    cells = ctypes.create_unicode_buffer(count + 1); read = wintypes.DWORD()
    assert k.ReadConsoleOutputCharacterW(out, cells, count, Coord(0, 0), ctypes.byref(read))
    text = cells.value
    assert text.count('CHAOS AGENT') == 1, repr((step, info.size.x, text))
    assert text.count('HISTORY_SENTINEL') == 1, repr((step, text))
    assert app.input.text == 'draft [image1]'
write('\x1b]0;RESIZE_DONE_OK\x07')
time.sleep(.1)
'''
        with ConsoleProcess(_program(body)) as console:
            for step, width in enumerate((160, 60, 140, 70, 100)):
                console.wait_for(f"READY_{step}")
                console.resize(width, 40)
                console.wait_for(f"READY_{step + 1}" if step < 4 else "RESIZE_DONE_OK")
