"""Verify press/release input reaches the real TUI exit loop through ConPTY."""
import os
import unittest

from code_agent.interfaces.tests.test_conpty_paste import _program

if os.name == "nt":
    from code_agent.interfaces.tests.conpty_support import ConsoleProcess


_CHORD = (
    "\x1b[17;29;0;1;8;1_"  # Ctrl down.
    "\x1b[67;46;3;1;8;1_"  # C down.
    "\x1b[67;46;3;0;8;1_"  # C up.
    "\x1b[17;29;0;0;0;1_"  # Ctrl up.
)
_BODY = r'''
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.windows_tui import WindowsTerminalApp
import code_agent.interfaces.windows_tui as terminal_app
async def check():
    def write(value):
        if '\x1b[?9001h' in value:
            print('READY', flush=True)
    app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=write)
    app.state.status = STATUS
    app.input.insert(DRAFT)
    original_handle_key = app.handle_key
    interrupts = 0
    drained = False
    def read_and_mark(*, timeout=None):
        nonlocal drained
        key = read_key(timeout=timeout)
        if key is None and interrupts == 1 and not drained:
            drained = True
            print('FIRST_TAP_DRAINED', flush=True)
        return key
    terminal_app.read_key = read_and_mark
    async def handle_key(key):
        nonlocal interrupts
        await original_handle_key(key)
        if key == '\x03':
            interrupts += 1
        if key == '\x1b':
            assert not app.composer_expanded
            print('ESC_COLLAPSED', flush=True)
    app.handle_key = handle_key
    await asyncio.wait_for(app.run(), timeout=2)
    assert interrupts == 2, interrupts
    assert not app.running and app.input.text == ''
    print('CLOSED_OK', flush=True)
asyncio.run(check())
'''


@unittest.skipUnless(os.name == "nt", "Windows ConPTY required")
class ConPtyCtrlCTests(unittest.TestCase):
    def test_two_complete_ctrl_c_taps_exit_with_or_without_escape(self):
        for payload in ("\x03", _CHORD):
            for status, draft, expanded in (
                ("idle", "", True),
                ("failed", ":", True),
                ("waiting_model", "", True),
                ("idle", "", False),
            ):
                with self.subTest(payload=repr(payload), status=status, expanded=expanded):
                    body = _BODY.replace("STATUS", repr(status)).replace("DRAFT", repr(draft))
                    with ConsoleProcess(_program(body)) as console:
                        console.wait_for("READY", timeout=3)
                        if not expanded:
                            console.send("\x1b[27;1;27;1;0;1_")
                            console.wait_for("ESC_COLLAPSED", timeout=3)
                        console.send(payload)
                        console.wait_for("FIRST_TAP_DRAINED", timeout=3)
                        console.send(payload)
                        console.wait_for("CLOSED_OK", timeout=3)
                        console.wait_for("RESTORED_OK", timeout=3)
