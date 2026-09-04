from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import ExitGuard, MAX_PASTE_BYTES, paste_event
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entries, render_entry, render_live_tail
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_status import status_context, status_presentation
from code_agent.interfaces.terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.task_mode_control import TaskModeControl
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class WindowsTerminalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_delegates_and_reports_persisted_checkpoint(self) -> None:
        class Engine(FakeEngine):
            async def compact_context(self, thread_id: str, cancellation: object):
                self.compacted = thread_id
                return SimpleNamespace(
                    before_messages=20,
                    after_messages=5,
                    before_tokens=10_000,
                    after_tokens=2_000,
                    checkpoint_id="semantic-test",
                    fallback_used=False,
                )

        engine = Engine(())
        app = WindowsTerminalApp(
            AgentController(engine), ApprovalBroker(), write=lambda _: None
        )
        app.current_thread_id = "thread-42"

        self.assertTrue(await app.submit("/compact"))

        self.assertEqual(engine.compacted, "thread-42")
        self.assertIn("checkpoint semantic-test persisted", app.state.entries[-1].text)

    async def test_blank_submit_without_attachments_is_a_quiet_noop(self) -> None:
        output: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=output.append,
        )

        self.assertFalse(await app.submit(""))
        self.assertEqual(app.state.entries, [])
        self.assertEqual(output, [])

    async def test_palette_executes_leaf_and_keeps_mode_as_a_root_parent(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.input.replace("/status")

        await app.handle_key("\r")

        self.assertEqual(app.input.text, "")
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.METADATA)

        app.modes = type("Modes", (), {"current": type("Mode", (), {"model": "test-model"})()})()
        app.input.replace("/mode")
        rows = app.interactions.rows(app)
        self.assertTrue(any("/mode" in row for row in rows))
        self.assertFalse(any("/mode low" in row for row in rows))

    async def test_default_help_matches_the_compact_root_surface(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)

        self.assertTrue(await app.submit("/help"))

        help_text = app.state.entries[-1].text
        self.assertIn("General\n", help_text)
        for name in (
            "status", "clear", "compact", "cost", "doctor",
            "exit", "diff", "review", "test", "rewind", "attach",
            "model", "mode", "effort", "permission", "mcp", "plugin", "tasks",
        ):
            self.assertIn(f":{name}", help_text)
        self.assertNotIn(":sessions", help_text)
        self.assertNotIn(":new", help_text)
        self.assertNotIn(":restore", help_text)
        self.assertNotIn(":evidence", help_text)

    async def test_help_all_includes_advanced_commands(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)

        self.assertTrue(await app.submit("/help all"))

        help_text = app.state.entries[-1].text
        self.assertIn(":sessions", help_text)
        self.assertIn(":evidence", help_text)
        self.assertIn(":restore", help_text)

    async def test_mode_prefix_enters_task_behavior_secondary_menu(self) -> None:
        class RuntimeSelection:
            current = type(
                "Selection",
                (),
                {
                    "topology": "single",
                    "profile": "gpt56_sol",
                    "model": "test-model",
                    "reasoning_effort": "medium",
                },
            )()

            @staticmethod
            def profiles():
                return (("gpt56_sol", "test-model", "chat_completions"),)

            async def use(self, **values):
                self.seen = values
                return self.current

        runtime = RuntimeSelection()
        task_modes = TaskModeControl()
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), task_modes=task_modes, write=lambda _: None)
        app.runtime_selection = runtime
        app.input.replace("/mode")

        await app.handle_key("\r")

        self.assertFalse(hasattr(runtime, "seen"))
        self.assertEqual(app.input.text, "/mode ")

        await app.handle_key("\r")

        self.assertFalse(hasattr(runtime, "seen"))
        self.assertEqual(task_modes.current.name, "ask")
        self.assertEqual(app.input.text, "")

    async def test_clear_keeps_the_current_thread_identity(self) -> None:
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.current_thread_id = "thread-42"
        app.state.entries.append(text_entry(DisplayKind.USER, "old transcript"))

        self.assertTrue(await app.submit("/clear"))

        self.assertEqual(app.current_thread_id, "thread-42")
        self.assertEqual(app.state.entries, [])
        self.assertTrue(any("\x1b[2J\x1b[H" in item for item in output))

    async def test_enter_submits_complete_restore_command_without_picker_replacement(self) -> None:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), history=object(), write=lambda _: None,
        )
        seen = []

        async def restore(thread_id: str) -> bool:
            seen.append(thread_id)
            return True

        app.restore_thread = restore
        app.input.replace("/restore T-042")

        await app.handle_key("\r")

        self.assertEqual(seen, ["T-042"])
        self.assertEqual(app.input.text, "")

    async def test_enter_switches_mode_and_updates_status_model(self) -> None:
        class Modes:
            current = type("Mode", (), {"name": "medium", "model": "old-model"})()

            async def use(self, name: str, *, idle: bool):
                self.seen = (name, idle)
                self.current = type("Mode", (), {"name": name, "model": "new-model"})()
                return self.current

        modes = Modes()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), modes=modes, write=lambda _: None,
        )
        app.input.replace("/mode high")

        await app.handle_key("\r")

        self.assertEqual(modes.seen, ("high", True))
        self.assertIn("mode selected: high · new-model", app.state.entries[-1].text)
        self.assertEqual(app.input.text, "")

    async def test_enter_switches_permission_without_approval(self) -> None:
        class Permissions:
            current = type("Permission", (), {"name": "ask"})()

            async def use(self, name: str, *, idle: bool):
                self.seen = (name, idle)
                self.current = type(
                    "Permission",
                    (),
                    {"name": name, "description": "完全访问"},
                )()
                return self.current

        permissions = Permissions()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            permissions=permissions,
            write=lambda _: None,
        )
        app.input.replace("/permission unrestricted")

        await app.handle_key("\r")

        self.assertEqual(permissions.seen, ("unrestricted", True))
        self.assertIn(
            "permission selected: unrestricted",
            app.state.entries[-1].text,
        )

if __name__ == "__main__": unittest.main()
