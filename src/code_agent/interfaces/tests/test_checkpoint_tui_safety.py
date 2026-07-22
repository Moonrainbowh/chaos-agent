from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from code_agent.checkpoints.models import RewindPreview, RewindResult
from code_agent.interfaces.command_registry import (
    CommandAction,
    CommandRegistry,
    CommandSpec,
)
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, display_width
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_lifecycle import close_tasks
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import RewindMode, RewindOperationStatus


TASK = "1" * 32
THREAD = "2" * 32
CHECKPOINT = "3" * 32
LINEAGE = "4" * 32
OPERATION = "5" * 32
DIGEST = "6" * 64


def record(label: str = "safe") -> CheckpointRecord:
    return CheckpointRecord(
        CHECKPOINT,
        THREAD,
        label,
        {"inventory_digest": DIGEST},
        datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


def preview(mode: str = "code") -> RewindPreview:
    return RewindPreview(
        OPERATION, TASK, LINEAGE, CHECKPOINT, RewindMode(mode), DIGEST,
        1, 0, 10, ("src/a.py",), True,
    )


class Control:
    def __init__(self, *, label: str = "safe") -> None:
        self.records = (record(label),)
        self.preview_calls = 0
        self.execute_calls = 0
        self.result_status = RewindOperationStatus.COMPLETED
        self.replacement: str | None = None

    async def list(self, task_id: str):
        return self.records

    async def create(self, task_id: str, label: str):
        return self.records[0]

    async def preview_rewind(self, task_id, checkpoint_id, mode="code"):
        self.preview_calls += 1
        return preview(mode)

    async def execute_rewind(self, value, *, confirmed):
        self.execute_calls += 1
        return RewindResult(
            OPERATION, TASK, self.replacement, self.result_status
        )


def app(control: object, registry: CommandRegistry | None = None) -> WindowsTerminalApp:
    value = WindowsTerminalApp(
        AgentController(FakeEngine(())),
        ApprovalBroker(),
        checkpoints=control,
        command_registry=registry or CommandRegistry(),
        write=lambda _: None,
    )
    value.active_task_id = TASK
    return value


async def reach_confirmation(value: WindowsTerminalApp, *, mode_moves: int = 0) -> None:
    await value.submit(f"/rewind {CHECKPOINT}")
    for _ in range(mode_moves):
        await value.handle_key("down")
    await value.handle_key("\r")
    await value.wait_checkpoint_idle()


class CheckpointTuiSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_repeats_target_mode_and_mode_specific_risk(self) -> None:
        expected = (
            (0, "code", "modifies workspace code"),
            (1, "session", "forks a replacement task and supersedes the old task"),
            (2, "code_and_session", "modifies workspace code and forks a replacement task"),
        )
        for moves, mode, impact in expected:
            with self.subTest(mode=mode):
                value = app(Control(label="pre-release label"))
                await reach_confirmation(value, mode_moves=moves)
                rows = "\n".join(value.interactions.rows(value))
                self.assertIn(f"checkpoint {CHECKPOINT}", rows)
                self.assertIn("label pre-release label", rows)
                self.assertIn(f"mode {mode}", rows)
                self.assertIn(impact, rows)
                self.assertIn("› No", rows)

    async def test_preview_is_single_flight_and_escape_cancels_without_leak(self) -> None:
        class SlowPreview(Control):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()

            async def preview_rewind(self, task_id, checkpoint_id, mode="code"):
                self.preview_calls += 1
                self.started.set()
                await asyncio.Event().wait()

        control = SlowPreview()
        value = app(control)
        await value.submit(f"/rewind {CHECKPOINT}")

        handling = asyncio.create_task(value.handle_key("\r"))
        await control.started.wait()
        try:
            self.assertTrue(handling.done(), "preview must not block the key chain")
            self.assertIn("previewing", "\n".join(value.interactions.rows(value)))
            await value.handle_key("\r")
            self.assertEqual(control.preview_calls, 1)
            await value.handle_key("\x1b")
            await value.wait_checkpoint_idle()
        finally:
            handling.cancel()
            await asyncio.gather(handling, return_exceptions=True)

        self.assertIsNone(value._rewind_task)
        self.assertIsNone(value._rewind_flow)

    async def test_execute_is_single_flight_and_close_waits_for_safe_completion(self) -> None:
        class SlowExecute(Control):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def execute_rewind(self, value, *, confirmed):
                self.execute_calls += 1
                self.started.set()
                await self.release.wait()
                return RewindResult(
                    OPERATION, TASK, None, RewindOperationStatus.COMPLETED
                )

        control = SlowExecute()
        value = app(control)
        await reach_confirmation(value)
        await value.handle_key("right")
        handling = asyncio.create_task(value.handle_key("\r"))
        await control.started.wait()
        try:
            self.assertTrue(handling.done(), "execute must not block the key chain")
            await value.handle_key("\r")
            self.assertIn("executing", "\n".join(value.interactions.rows(value)))
            self.assertEqual(control.execute_calls, 1)

            closing = asyncio.create_task(close_tasks(value))
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            closing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done(), "close cancellation must not abort execute")
            control.release.set()
            await closing
            self.assertIsNone(value._rewind_task)
        finally:
            control.release.set()
            handling.cancel()
            await asyncio.gather(handling, return_exceptions=True)

    async def test_service_errors_are_in_band_bounded_and_leave_tui_usable(self) -> None:
        class Broken(Control):
            async def list(self, task_id):
                raise RuntimeError("bad\x1b[2J" + "x" * 1000)

            async def create(self, task_id, label):
                raise RuntimeError("bad\x1b[2J" + "x" * 1000)

        for command in ("/checkpoint list", "/checkpoint create save", "/rewind"):
            with self.subTest(command=command):
                value = app(Broken())
                self.assertFalse(await value.submit(command))
                entry = value.state.entries[-1]
                self.assertEqual(entry.kind, DisplayKind.ERROR)
                self.assertNotIn("\x1b[2J", entry.text)
                self.assertLessEqual(display_width(entry.text), 200)
                self.assertTrue(await value.submit("/status"))

    async def test_long_existing_label_is_safely_bounded_in_list_and_picker(self) -> None:
        value = app(Control(label="标签" * 400 + "\x1b[2J"))
        self.assertTrue(await value.submit("/checkpoint list"))
        self.assertLessEqual(display_width(value.state.entries[-1].text), 512)
        self.assertNotIn("\x1b[2J", value.state.entries[-1].text)

        self.assertTrue(await value.submit("/rewind"))
        rows = value.interactions.rows(value)
        self.assertTrue(all(display_width(row) <= value._columns() for row in rows))

    async def test_noncompleted_result_never_switches_replacement_task(self) -> None:
        for status in (
            RewindOperationStatus.ROLLED_BACK,
            RewindOperationStatus.RECOVERY_REQUIRED,
        ):
            with self.subTest(status=status):
                control = Control()
                control.result_status = status
                control.replacement = "7" * 32
                value = app(control)
                await reach_confirmation(value)
                await value.handle_key("right")
                await value.handle_key("\r")
                await value.wait_checkpoint_idle()
                self.assertEqual(value.active_task_id, TASK)

    async def test_custom_checkpoint_alias_executes_canonical_action(self) -> None:
        registry = CommandRegistry((
            CommandSpec(
                "检查点", ("checkpoint",), "工作区", "checkpoint", "<action>",
                requires=("checkpoints",),
                actions=(CommandAction("列表", ("browse",), "list"),),
            ),
        ))
        control = Control()
        value = app(control, registry)

        self.assertTrue(await value.submit("/checkpoint browse"))
        self.assertIn(CHECKPOINT, value.state.entries[-1].text)


if __name__ == "__main__":
    unittest.main()
