"""Exercise paste through ConPTY translation, not injected pretranslated keys."""
import os
import textwrap
import unittest

if os.name == "nt":
    from code_agent.interfaces.tests.conpty_support import ConsoleProcess


_PROLOGUE = r'''
import asyncio, ctypes, json, msvcrt, sys, traceback
from ctypes import wintypes
sys.stdout = open('CONOUT$', 'w', encoding='utf-8')
sys.stderr = sys.stdout
import faulthandler
faulthandler.dump_traceback_later(7, file=sys.stderr)
sys.stdin = open('CONIN$', 'r')
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
k.SetStdHandle(-10, msvcrt.get_osfhandle(sys.stdin.fileno()))
k.SetStdHandle(-11, msvcrt.get_osfhandle(sys.stdout.fileno()))
k.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
h = msvcrt.get_osfhandle(sys.stdin.fileno())
before = wintypes.DWORD()
assert k.GetConsoleMode(h, ctypes.byref(before))
from code_agent.interfaces.terminal_io import read_key, capture_ctrl_c_as_input
from code_agent.interfaces.startup_splash import _enable_vt
restore_out = _enable_vt(sys.stdout)
restore = capture_ctrl_c_as_input()
print('\x1b[?9001h', end='', flush=True)
'''
_EPILOGUE = r'''
finally:
    faulthandler.cancel_dump_traceback_later()
    restore()
    after = wintypes.DWORD()
    assert k.GetConsoleMode(h, ctypes.byref(after))
    assert before.value == after.value
    print('\x1b[?9001l\x1b[?2004lRESTORED_OK', flush=True)
    restore_out()
'''


def _program(body):
    return _PROLOGUE + "\ntry:\n" + textwrap.indent(body, "    ") + _EPILOGUE


@unittest.skipUnless(os.name == "nt", "Windows ConPTY required")
class ConPtyPasteTests(unittest.TestCase):
    def test_multiline_right_click_payload_waits_for_explicit_enter(self):
        payload = "\r\n" + "\r\n".join(f"第 {i} 行：只测试粘贴，不执行命令。😀" for i in range(20)) + "\r\n"
        body = r'''
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.windows_tui import WindowsTerminalApp
async def check():
    engine = FakeEngine(())
    app = WindowsTerminalApp(AgentController(engine), ApprovalBroker(), write=lambda _: None)
    print('\x1b[?2004hREADY', flush=True)
    event = await asyncio.to_thread(read_key, timeout=5)
    assert event.startswith('\x1b[200~') and event.endswith('\x1b[201~'), repr(event)
    await app.handle_key(event)
    assert app.input.text == EXPECTED, repr(app.input.text)
    assert app._run_task is None and not engine.calls
    assert '[chars:' in app.input.display[0]
    print('DRAFT_OK', flush=True)
    key = await asyncio.to_thread(read_key, timeout=5)
    while key == '': key = read_key(timeout=5)
    assert key == '\r', repr(key)
    await app.handle_key(key)
    await app.wait_idle()
    assert len(engine.calls) == 1
    assert engine.calls[0][0] == EXPECTED
    print('SUBMITTED_ONCE_OK', flush=True)
asyncio.run(check())
'''.replace("EXPECTED", repr(payload.replace("\r\n", "\n")))
        with ConsoleProcess(_program(body)) as console:
            console.wait_for("READY")
            console.send("\x1b[200~" + payload + "\x1b[201~")
            # Hosted Windows ConPTY may translate large Unicode pastes slowly.
            console.wait_for("DRAFT_OK", timeout=30)
            console.send("\r")
            console.wait_for("SUBMITTED_ONCE_OK")
            console.wait_for("RESTORED_OK")

    def test_typing_followed_immediately_by_enter_keeps_submit_separate(self):
        body = r'''
from code_agent.interfaces.input_events import paste_event
print('READY', flush=True)
text = ''
while True:
    key = read_key(timeout=5)
    assert key is not None
    if key == '\r':
        break
    text += paste_event(key[6:-6]).value if key.startswith('\x1b[200~') else key
assert text == 'hello😀', repr(text)
print('TYPED_ENTER_OK', flush=True)
'''
        with ConsoleProcess(_program(body)) as console:
            console.wait_for("READY")
            console.send("hello😀\r")
            console.wait_for("TYPED_ENTER_OK")
            console.wait_for("RESTORED_OK")

    def test_vt_navigation_and_native_shortcuts_survive_mode_change(self):
        sequences = (
            ("\x1b[A", "up"), ("\x1b[B", "down"), ("\x1b[D", "left"), ("\x1b[C", "right"),
            ("\x1b[H", "home"), ("\x1b[F", "end"), ("\x1b[3~", "delete"),
            ("\x1bOA", "up"), ("\x1bOF", "end"),
            ("\x1b[13;28;13;1;16;1_", "shift+enter"),
            ("\x1b[13;28;13;1;0;1_", "\r"),
            ("\x1b[86;47;118;1;2;1_", "alt+v"),
            ("\x1b[67;46;3;1;8;1_", "\x03"),
        )
        body = "print('READY', flush=True)\n"
        for i, (_, expected) in enumerate(sequences):
            body += f"key = read_key(timeout=5)\nwhile key == '': key = read_key(timeout=5)\nassert key == {expected!r}, ({i}, key)\nprint('KEY_{i}_OK', flush=True)\n"
        with ConsoleProcess(_program(body)) as console:
            console.wait_for("READY")
            for i, (sequence, _) in enumerate(sequences):
                console.send(sequence)
                console.wait_for(f"KEY_{i}_OK")
            console.wait_for("RESTORED_OK")
