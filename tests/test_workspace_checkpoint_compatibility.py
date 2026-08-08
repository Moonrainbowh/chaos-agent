from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.workspace_models import RewindOperationStatus
from tests.agent_app_test_support import _configured_application, _init_git_source


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
        digest.update(str(path.stat().st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


class WorkspaceCheckpointCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_recovers_pending_rewind_from_read_only_legacy_blobs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(
                root, workspace_storage_name="current-managed-workspaces"
            )
            task = await application.foreground_tasks.start("compat recovery")
            task_root = Path(task.contract.authorization.workspace_root)
            (task_root / "note.py").write_text(
                "print('rollback')\n", encoding="utf-8"
            )
            rollback = await application.tui.checkpoints.create(
                task.id, "legacy rollback"
            )
            preview = await application.tui.checkpoints.preview_rewind(
                task.id, rollback.id, "code"
            )
            operation = await application.tui.sessions.begin_rewind(
                preview, rollback.id
            )
            (task_root / "note.py").write_text(
                "print('crash')\n", encoding="utf-8"
            )

            current = application.workspace_runtime.storage_root / "snapshots"
            legacy = current.parent.parent / "managed-workspaces" / "snapshots"
            shutil.copytree(current, legacy)
            shutil.rmtree(current)
            legacy_before = _tree_digest(legacy)
            await application.aclose()

            restarted = _configured_application(
                root, workspace_storage_name="current-managed-workspaces"
            )
            results = await restarted.workspace_runtime.startup()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].operation_id, operation.id)
            self.assertEqual(
                results[0].status, RewindOperationStatus.ROLLED_BACK
            )
            self.assertEqual(
                (task_root / "note.py").read_text(encoding="utf-8"),
                "print('rollback')\n",
            )
            self.assertEqual(
                await restarted.tui.sessions.pending_rewinds(), ()
            )
            self.assertFalse(current.exists())
            self.assertEqual(_tree_digest(legacy), legacy_before)
            await restarted.aclose()

            idempotent = _configured_application(
                root, workspace_storage_name="current-managed-workspaces"
            )
            self.assertEqual(await idempotent.workspace_runtime.startup(), ())
            self.assertEqual(
                (task_root / "note.py").read_text(encoding="utf-8"),
                "print('rollback')\n",
            )
            self.assertEqual(_tree_digest(legacy), legacy_before)
            await idempotent.aclose()


if __name__ == "__main__":
    unittest.main()
