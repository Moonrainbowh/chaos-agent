from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from code_agent.checkpoints.models import (
    RewindConfirmationRequired,
    RewindPreview,
    RewindRecoveryRequired,
    RewindResult,
)
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.checkpoint_control import (
    CheckpointControl,
    checkpoint_record_to_json,
    rewind_preview_to_json,
    rewind_result_to_json,
)
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import (
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
)
from code_agent.interfaces.terminal_display import DisplayKind, display_width
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


TASK = "1" * 32
THREAD = "2" * 32
CHECKPOINT = "3" * 32
LINEAGE = "4" * 32
OPERATION = "5" * 32
DIGEST = "6" * 64


def checkpoint(*, available: bool = True) -> CheckpointRecord:
    return CheckpointRecord(
        CHECKPOINT,
        THREAD,
        "before refactor",
        {
            "inventory_digest": DIGEST if available else "",
            "file_count": 2,
            "total_bytes": 12,
        },
        datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


def preview(mode: RewindMode = RewindMode.CODE) -> RewindPreview:
    return RewindPreview(
        OPERATION,
        TASK,
        LINEAGE,
        CHECKPOINT,
        mode,
        DIGEST,
        2,
        1,
        12,
        ("src/a.py", "src/旧.py"),
        True,
    )


class FakeCheckpoints:
    def __init__(self) -> None:
        self.records = (checkpoint(),)

    async def list(self, task_id: str) -> tuple[CheckpointRecord, ...]:
        self.listed = task_id
        return self.records

    async def capture(self, task_id: str, label: str) -> CheckpointRecord:
        self.captured = (task_id, label)
        return self.records[0]


class FakeRewind:
    async def preview(
        self, task_id: str, checkpoint_id: str, mode: RewindMode
    ) -> RewindPreview:
        self.previewed = (task_id, checkpoint_id, mode)
        return preview(mode)

    async def execute(
        self, value: RewindPreview, *, confirmed: bool
    ) -> RewindResult:
        self.executed = (value, confirmed)
        return RewindResult(
            OPERATION, TASK, None, RewindOperationStatus.COMPLETED
        )

    async def recover_operation(self, operation_id: str) -> RewindResult:
        self.recovered = operation_id
        return RewindResult(
            operation_id, TASK, None, RewindOperationStatus.ROLLED_BACK
        )


class FakeCheckpointControl:
    def __init__(self, records: tuple[CheckpointRecord, ...]) -> None:
        self.records = records

    async def list(self, task_id: str) -> tuple[CheckpointRecord, ...]:
        self.listed = task_id
        return self.records

    async def create(self, task_id: str, label: str) -> CheckpointRecord:
        self.created = (task_id, label)
        return self.records[0]

    async def preview_rewind(
        self, task_id: str, checkpoint_id: str, mode: str = "code"
    ) -> RewindPreview:
        self.previewed = (task_id, checkpoint_id, mode)
        paths = tuple(f"src/file-{index}.py" for index in range(12))
        return RewindPreview(
            OPERATION, TASK, LINEAGE, checkpoint_id, RewindMode(mode), DIGEST,
            8, 4, 4096, paths, True,
        )

    async def execute_rewind(
        self, value: RewindPreview, *, confirmed: bool
    ) -> RewindResult:
        self.executed = (value, confirmed)
        return RewindResult(
            OPERATION, TASK, None, RewindOperationStatus.COMPLETED
        )


class CheckpointControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.checkpoints = FakeCheckpoints()
        self.rewind = FakeRewind()
        self.control = CheckpointControl(self.checkpoints, self.rewind)

    async def test_list_and_create_delegate_only_typed_values(self) -> None:
        self.assertEqual(await self.control.list(TASK), (checkpoint(),))
        created = await self.control.create(TASK, "manual")

        self.assertEqual(created.id, CHECKPOINT)
        self.assertEqual(self.checkpoints.listed, TASK)
        self.assertEqual(self.checkpoints.captured, (TASK, "manual"))

    async def test_create_rejects_blank_and_oversized_labels(self) -> None:
        for label in ("", " " * 3, "x" * 161):
            with self.subTest(size=len(label)):
                with self.assertRaises(ValueError):
                    await self.control.create(TASK, label)
        self.assertFalse(hasattr(self.checkpoints, "captured"))

    async def test_preview_defaults_to_code_and_accepts_three_modes(self) -> None:
        default = await self.control.preview_rewind(TASK, CHECKPOINT)
        self.assertEqual(default.mode, RewindMode.CODE)

        for value in ("code", "session", "code_and_session"):
            with self.subTest(value=value):
                result = await self.control.preview_rewind(TASK, CHECKPOINT, value)
                self.assertEqual(result.mode, RewindMode(value))

    async def test_execute_requires_literal_true_before_delegating(self) -> None:
        for value in (False, 1, "yes"):
            with self.subTest(value=value):
                with self.assertRaises(RewindConfirmationRequired):
                    await self.control.execute_rewind(preview(), confirmed=value)

        self.assertFalse(hasattr(self.rewind, "executed"))
        result = await self.control.execute_rewind(preview(), confirmed=True)
        self.assertEqual(result.status, RewindOperationStatus.COMPLETED)

    async def test_recover_rewind_delegates_typed_pending_operation(self) -> None:
        operation = RewindOperationRecord.create(
            LINEAGE,
            CHECKPOINT,
            "7" * 32,
            RewindMode.CODE,
            DIGEST,
            operation_id=OPERATION,
        )

        result = await self.control.recover_rewind(operation.id)

        self.assertEqual(self.rewind.recovered, operation.id)
        self.assertEqual(result.status, RewindOperationStatus.ROLLED_BACK)

    def test_checkpoint_preview_and_result_payloads_are_json_safe(self) -> None:
        nested = CheckpointRecord(
            CHECKPOINT,
            THREAD,
            "nested",
            {"inventory_digest": DIGEST, "summary": {"paths": ["src/旧.py"]}},
        )
        result = RewindResult(
            OPERATION, TASK, "7" * 32, RewindOperationStatus.COMPLETED
        )
        payloads = (
            checkpoint_record_to_json(nested),
            rewind_preview_to_json(preview()),
            rewind_result_to_json(result),
        )

        encoded = json.dumps(payloads, ensure_ascii=False)

        self.assertIn("src/旧.py", encoded)
        self.assertEqual(payloads[1]["mode"], "code")
        self.assertEqual(payloads[2]["status"], "completed")


class CheckpointTuiTests(unittest.IsolatedAsyncioTestCase):
    def app(self, control: object) -> WindowsTerminalApp:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            checkpoints=control,
            write=lambda _: None,
        )
        app.active_task_id = TASK
        return app

    async def test_checkpoint_list_and_create_render_safe_results(self) -> None:
        control = FakeCheckpointControl((checkpoint(), checkpoint(available=False)))
        app = self.app(control)

        self.assertTrue(await app.submit("/checkpoint list"))
        self.assertIn("before refactor", app.state.entries[-1].text)
        self.assertIn("code unavailable", app.state.entries[-1].text)

        self.assertTrue(await app.submit("/检查点 创建 发布前"))
        self.assertEqual(control.created, (TASK, "发布前"))
        self.assertIn(CHECKPOINT, app.state.entries[-1].text)

    async def test_rewind_picker_disables_code_modes_with_a_reason(self) -> None:
        unavailable = checkpoint(available=False)
        control = FakeCheckpointControl((unavailable,))
        app = self.app(control)

        self.assertTrue(await app.submit("/rewind"))
        self.assertIn("code unavailable", "\n".join(app.interactions.rows(app)))
        await app.handle_key("\r")
        mode_rows = app.interactions.rows(app)

        self.assertIn("› 仅代码", mode_rows[0])
        self.assertIn("checkpoint has no recoverable code", mode_rows[0])
        self.assertTrue(any("仅会话" in row for row in mode_rows))
        self.assertTrue(any("代码与会话" in row for row in mode_rows))

    async def test_rewind_defaults_to_code_previews_and_confirmation_defaults_no(self) -> None:
        control = FakeCheckpointControl((checkpoint(),))
        app = self.app(control)

        await app.submit(f"/rewind {CHECKPOINT}")
        self.assertIn("› 仅代码", app.interactions.rows(app)[0])
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        rows = app.interactions.rows(app)
        self.assertEqual(control.previewed, (TASK, CHECKPOINT, "code"))
        self.assertTrue(any("restore 8 · delete 4 · 4096 bytes" in row for row in rows))
        self.assertIn("› No", rows[-3])
        self.assertLessEqual(sum("src/file-" in row for row in rows), 6)

        await app.handle_key("\r")
        self.assertFalse(hasattr(control, "executed"))
        self.assertIn("cancelled", app.state.entries[-1].text)

    async def test_preview_rows_are_width_and_byte_bounded(self) -> None:
        class LongPathControl(FakeCheckpointControl):
            async def preview_rewind(self, task_id, checkpoint_id, mode="code"):
                return RewindPreview(
                    OPERATION, TASK, LINEAGE, checkpoint_id, RewindMode(mode),
                    DIGEST, 1, 0, 1, ("x" * 500 + "\x1b[2J",), True,
                )

        app = self.app(LongPathControl((checkpoint(),)))
        await app.submit(f"/rewind {CHECKPOINT}")
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        rows = app.interactions.rows(app)
        self.assertLessEqual(len(rows), 10)
        self.assertLessEqual(sum(len(row.encode("utf-8")) for row in rows), 16_384)
        self.assertTrue(all(display_width(row) <= app._columns() for row in rows))
        self.assertNotIn("\x1b[2J", "\n".join(rows))

    async def test_confirmed_rewind_shows_progress_and_result(self) -> None:
        control = FakeCheckpointControl((checkpoint(),))
        app = self.app(control)
        await app.submit(f"/rewind {CHECKPOINT}")
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        await app.handle_key("right")
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        self.assertEqual(control.executed[1], True)
        transcript = "\n".join(entry.text for entry in app.state.entries)
        self.assertIn("rewind in progress", transcript)
        self.assertIn("rewind completed", transcript)
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.SUCCESS)

    async def test_recovery_required_is_rendered_as_a_guard(self) -> None:
        class RecoveryControl(FakeCheckpointControl):
            async def execute_rewind(self, value, *, confirmed):
                raise RewindRecoveryRequired(("locked.py",))

        app = self.app(RecoveryControl((checkpoint(),)))
        await app.submit(f"/rewind {CHECKPOINT}")
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        await app.handle_key("right")
        await app.handle_key("\r")
        await app.wait_checkpoint_idle()
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)
        self.assertIn("recovery-required", app.state.entries[-1].text)
        self.assertIn("locked.py", app.state.entries[-1].text)


if __name__ == "__main__":
    unittest.main()
