from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import ANY

from code_agent.checkpoints.models import (
    RewindConfirmationRequired,
    RewindConflict,
    RewindRecoveryRequired,
    RewindUnavailable,
)
from code_agent.checkpoints.rewind import RewindCoordinator
from code_agent.checkpoints.service import CheckpointService
from code_agent.core.task import TaskStatus
from code_agent.sessions.workspace_models import RewindMode, RewindOperationStatus
from code_agent.workspace.errors import FileTooLargeError

from code_agent.checkpoints.tests._support import (
    FakeLocks,
    FakeSessions,
    FakeWorkspace,
    identifier,
)


class RewindCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.workspace = FakeWorkspace()
        self.sessions = FakeSessions(self.workspace)
        self.locks = FakeLocks()

        async def quiesce(task_id: str) -> None:
            self.workspace.order.append("quiesce")

        self.service = CheckpointService(
            self.sessions, self.workspace, quiesce, self.locks
        )
        self.target = await self.service.capture(self.sessions.task.id, "target")
        self.invalidations: list[tuple[str, ...]] = []
        self.verification: list[tuple[str, str | None]] = []
        self.coordinator = RewindCoordinator(
            self.sessions, self.workspace, self.service, quiesce, self.locks,
            self.invalidations.append,
            lambda task_id, replacement: self.verification.append((task_id, replacement)),
        )

    async def test_unconfirmed_execute_has_no_side_effect(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        before = len(self.sessions.checkpoints)
        with self.assertRaises(RewindConfirmationRequired):
            await self.coordinator.execute(preview, confirmed=1)
        self.assertEqual(len(self.sessions.checkpoints), before)
        self.assertEqual(self.workspace.restore_calls, [])

    async def test_stale_preview_is_rolled_back_without_restore(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        self.workspace.files["changed.py"] = b"changed"
        with self.assertRaisesRegex(RewindConflict, "preview is stale"):
            await self.coordinator.execute(preview, confirmed=True)
        operation = self.sessions.operations[preview.operation_id]
        self.assertEqual(operation.status, RewindOperationStatus.ROLLED_BACK)
        self.assertEqual(self.workspace.restore_calls, [])
        self.assertEqual(self.invalidations, [])

    async def test_code_rewind_restores_and_invalidates_after_success(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        result = await self.coordinator.execute(preview, confirmed=True)
        self.assertEqual(result.status, RewindOperationStatus.COMPLETED)
        self.assertEqual(self.workspace.restore_calls, [self.target.id])
        self.assertEqual(self.invalidations, [()])
        self.assertEqual(self.verification, [(self.sessions.task.id, None)])

    async def test_invalidation_precedes_completed_terminal_state(self) -> None:
        order = self.sessions.completion_order
        coordinator = RewindCoordinator(
            self.sessions,
            self.workspace,
            self.service,
            self.service.quiesce,
            self.locks,
            lambda _: order.append("cache"),
            lambda *_: order.append("verification"),
        )
        preview = await coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )

        await coordinator.execute(preview, confirmed=True)

        self.assertEqual(order, ["cache", "verification", "complete"])

    async def test_invalidation_failure_never_leaves_completed_operation(self) -> None:
        def fail_invalidation(_: tuple[str, ...]) -> None:
            raise RuntimeError("cache invalidation failed")

        coordinator = RewindCoordinator(
            self.sessions,
            self.workspace,
            self.service,
            self.service.quiesce,
            self.locks,
            fail_invalidation,
            lambda *_: None,
        )
        preview = await coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )

        with self.assertRaises(RewindRecoveryRequired):
            await coordinator.execute(preview, confirmed=True)

        self.assertEqual(
            self.sessions.operations[preview.operation_id].status,
            RewindOperationStatus.RECOVERY_REQUIRED,
        )

    async def test_combined_fork_failure_rolls_back_changed_code(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE_AND_SESSION
        )
        self.sessions.fail_fork = True
        with self.assertRaises(Exception):
            await self.coordinator.execute(preview, confirmed=True)
        self.assertEqual(self.workspace.restore_calls, [self.target.id, ANY])
        self.assertEqual(
            self.sessions.operations[preview.operation_id].status,
            RewindOperationStatus.ROLLED_BACK,
        )

    async def test_unavailable_pre_rewind_never_begins_or_mutates(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        self.workspace.inventory_failure = FileTooLargeError("rollback limit")

        with self.assertRaises(RewindUnavailable):
            await self.coordinator.execute(preview, confirmed=True)

        self.assertEqual(self.sessions.operations, {})
        self.assertEqual(self.workspace.restore_calls, [])

    async def test_session_rewind_transfers_owner_and_supersedes_old_task(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.SESSION
        )
        result = await self.coordinator.execute(preview, confirmed=True)
        self.assertIsNotNone(result.replacement_task_id)
        self.assertEqual(self.sessions.task.status, TaskStatus.SUPERSEDED)
        self.assertEqual(self.sessions.lineage.owner_task_id, result.replacement_task_id)
        self.assertEqual(len(self.sessions.atomic_session_calls), 1)

    async def test_session_preview_remains_legal_when_code_is_unavailable(self) -> None:
        self.workspace.inventory_failure = FileTooLargeError("limit")
        checkpoint = await self.service.capture(self.sessions.task.id, "session-only")
        self.workspace.inventory_failure = None

        preview = await self.coordinator.preview(
            self.sessions.task.id, checkpoint.id, RewindMode.SESSION
        )

        self.assertFalse(preview.code_available)

    async def test_code_preview_rejects_unavailable_checkpoint(self) -> None:
        self.workspace.inventory_failure = FileTooLargeError("limit")
        checkpoint = await self.service.capture(self.sessions.task.id, "session-only")
        self.workspace.inventory_failure = None

        with self.assertRaises(RewindUnavailable):
            await self.coordinator.preview(
                self.sessions.task.id, checkpoint.id, RewindMode.CODE
            )

    async def test_blob_corruption_after_preview_fails_before_mutation(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        snapshot = self.workspace.snapshots[self.target.id]
        self.workspace.corrupt.add(snapshot.id)
        with self.assertRaises(RewindUnavailable):
            await self.coordinator.execute(preview, confirmed=True)
        self.assertEqual(self.workspace.restore_calls, [])
        self.assertEqual(self.invalidations, [])

    async def test_preview_tampering_is_rejected_before_intent(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        tampered = replace(preview, total_bytes=preview.total_bytes + 1)
        with self.assertRaisesRegex(RewindConflict, "tampered"):
            await self.coordinator.execute(tampered, confirmed=True)
        self.assertEqual(self.sessions.operations, {})

    async def test_operation_id_tampering_is_rejected_without_intent(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE
        )
        tampered = replace(preview, operation_id=identifier())
        with self.assertRaisesRegex(RewindConflict, "not issued"):
            await self.coordinator.execute(tampered, confirmed=True)
        self.assertEqual(self.sessions.operations, {})
        self.assertEqual(self.workspace.restore_calls, [])

    async def test_rollback_failure_marks_recovery_required_and_blocks_lineage(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE_AND_SESSION
        )
        self.sessions.fail_fork = True
        self.workspace.fail_restore_at = 2
        blocked: list[tuple[str, tuple[str, ...], str]] = []
        self.coordinator.block_lineage = lambda *args: blocked.append(args)
        with self.assertRaises(RewindRecoveryRequired):
            await self.coordinator.execute(preview, confirmed=True)
        self.assertEqual(
            self.sessions.operations[preview.operation_id].status,
            RewindOperationStatus.RECOVERY_REQUIRED,
        )
        self.assertEqual(self.sessions.lineage.status.value, "recovery_required")
        self.assertTrue(blocked)
        self.assertEqual(self.invalidations, [()])

    async def test_recovery_paths_obey_count_and_utf8_byte_limits(self) -> None:
        self.workspace.files = {
            f"{index:03}-{'x' * 190}": b"x" for index in range(100)
        }
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.CODE_AND_SESSION
        )
        self.sessions.fail_fork = True
        self.workspace.fail_restore_at = 2

        with self.assertRaises(RewindRecoveryRequired) as raised:
            await self.coordinator.execute(preview, confirmed=True)

        paths = raised.exception.paths
        self.assertLessEqual(len(paths), 100)
        self.assertLessEqual(sum(len(path.encode("utf-8")) for path in paths), 16_384)
        self.assertLessEqual(len(str(raised.exception).encode("utf-8")), 17_000)

    async def test_pending_recovery_always_restores_rollback_and_marks_rolled_back(self) -> None:
        preview = await self.coordinator.preview(
            self.sessions.task.id, self.target.id, RewindMode.SESSION
        )
        rollback = await self.service.capture(self.sessions.task.id, "pre-rewind")
        await self.sessions.begin_rewind(preview, rollback.id)
        self.workspace.files["later.py"] = b"later"

        results = await self.coordinator.recover_pending()

        self.assertEqual(results[0].status, RewindOperationStatus.ROLLED_BACK)
        self.assertEqual(self.workspace.restore_calls, [rollback.id])
        self.assertNotIn("later.py", self.workspace.files)
        self.assertEqual(self.invalidations, [()])


if __name__ == "__main__":
    unittest.main()
