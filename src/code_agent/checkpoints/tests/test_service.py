from __future__ import annotations

import unittest

from code_agent.checkpoints.service import CheckpointService
from code_agent.sessions.workspace_models import WorkspaceSnapshotStatus
from code_agent.workspace.errors import FileTooLargeError

from code_agent.checkpoints.tests._support import FakeLocks, FakeSessions, FakeWorkspace


class CheckpointServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_quiesces_before_inventory_and_publishes_available(self) -> None:
        workspace = FakeWorkspace()
        sessions = FakeSessions(workspace)

        async def quiesce(task_id: str) -> None:
            workspace.order.append("quiesce")

        service = CheckpointService(sessions, workspace, quiesce, FakeLocks())
        checkpoint = await service.capture(sessions.task.id, "paused")

        self.assertEqual(workspace.order[:2], ["quiesce", "inventory"])
        self.assertEqual(
            sessions.cursors[checkpoint.id].snapshot_status,
            WorkspaceSnapshotStatus.AVAILABLE,
        )

    async def test_explicit_capture_limit_publishes_session_only_checkpoint(self) -> None:
        workspace = FakeWorkspace()
        workspace.inventory_failure = FileTooLargeError("limit")
        sessions = FakeSessions(workspace)
        service = CheckpointService(sessions, workspace, _noop, FakeLocks())

        checkpoint = await service.capture(sessions.task.id, "limited")

        self.assertEqual(
            sessions.cursors[checkpoint.id].snapshot_status,
            WorkspaceSnapshotStatus.UNAVAILABLE,
        )
        self.assertNotIn(checkpoint.id, workspace.snapshots)

    async def test_io_failure_is_not_downgraded_to_unavailable(self) -> None:
        workspace = FakeWorkspace()
        workspace.inventory_failure = OSError("disk failed")
        sessions = FakeSessions(workspace)
        service = CheckpointService(sessions, workspace, _noop, FakeLocks())

        with self.assertRaises(OSError):
            await service.capture(sessions.task.id, "broken")
        self.assertEqual(sessions.checkpoints, {})


async def _noop(task_id: str) -> None:
    return None


if __name__ == "__main__":
    unittest.main()
