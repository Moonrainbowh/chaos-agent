from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime  # noqa: E402
from tests.agent_app_test_support import _init_git_source  # noqa: E402


class _FailingLineageSessions:
    async def create_lineage(self, record) -> None:
        del record
        raise RuntimeError("lineage write failed")

    async def load_lineage(self, lineage_id: str):
        raise SessionNotFound(lineage_id)


class _CommittedThenFailedSessions:
    def __init__(self) -> None:
        self.record = None

    async def create_lineage(self, record) -> None:
        self.record = record
        raise RuntimeError("lineage acknowledgement lost")

    async def load_lineage(self, lineage_id: str):
        if self.record is None or self.record.id != lineage_id:
            raise SessionNotFound(lineage_id)
        return self.record


class _BlockingCommitSessions:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.record = None

    async def create_lineage(self, record) -> None:
        self.entered.set()
        await self.release.wait()
        self.record = record

    async def load_lineage(self, lineage_id: str):
        if self.record is None or self.record.id != lineage_id:
            raise SessionNotFound(lineage_id)
        return self.record


class ManagedWorktreeCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_lifecycle_lock_wait_leaves_no_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            runtime = ManagedWorkspaceRuntime(object(), root / "state")
            identity = runtime._worktrees.identify(source)
            entered = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            real_create = runtime._worktrees.create

            def tracked_create(*args, **kwargs):
                try:
                    return real_create(*args, **kwargs)
                finally:
                    finished.set()

            def hold_lifecycle_lock() -> None:
                with runtime._worktrees._repository_lock(identity.repository_id):
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError("lifecycle lock was not released")

            holder = threading.Thread(target=hold_lifecycle_lock, daemon=True)
            holder.start()
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            with patch.object(runtime._worktrees, "create", tracked_create):
                prepare = asyncio.create_task(runtime.prepare_task(source, "pending"))
                await asyncio.sleep(0.05)
                prepare.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await prepare
                settled_before_return = finished.is_set()
            holder.join(2)
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            self.assertTrue(settled_before_return)

            self._assert_no_unclaimed_worktree(runtime, source, identity.repository_id)
            runtime.close()

    async def test_cancel_during_seed_compensates_partial_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            (source / "note.py").write_text("dirty\n", encoding="utf-8")
            runtime = ManagedWorkspaceRuntime(object(), root / "state")
            identity = runtime._worktrees.identify(source)
            entered = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            real_restore = WorkspaceEditor.restore

            def blocked_restore(editor, snapshot, **kwargs):
                (editor.guard.root / "partial.tmp").write_text(
                    "partial", encoding="utf-8"
                )
                entered.set()
                if not release.wait(2):
                    raise AssertionError("seed restore was not released")
                try:
                    return real_restore(editor, snapshot, **kwargs)
                finally:
                    finished.set()

            with patch.object(WorkspaceEditor, "restore", blocked_restore):
                prepare = asyncio.create_task(
                    runtime.prepare_task(source, "pending")
                )
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                prepare.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await prepare
                settled_before_return = finished.is_set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            self.assertTrue(settled_before_return)

            self._assert_no_unclaimed_worktree(runtime, source, identity.repository_id)
            runtime.close()

    async def test_seed_failure_compensates_partial_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            runtime = ManagedWorkspaceRuntime(object(), root / "state")
            identity = runtime._worktrees.identify(source)

            def failed_restore(editor, snapshot, **kwargs):
                del snapshot, kwargs
                (editor.guard.root / "partial.tmp").write_text(
                    "partial", encoding="utf-8"
                )
                raise RuntimeError("seed failed")

            with patch.object(WorkspaceEditor, "restore", failed_restore):
                with self.assertRaisesRegex(RuntimeError, "seed failed"):
                    await runtime.prepare_task(source, "pending")

            self._assert_no_unclaimed_worktree(runtime, source, identity.repository_id)
            runtime.close()

    async def test_lineage_write_failure_discards_unclaimed_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            runtime = ManagedWorkspaceRuntime(
                _FailingLineageSessions(), root / "state"
            )
            identity = runtime._worktrees.identify(source)
            workspace = await runtime.prepare_task(source, "pending")

            with self.assertRaisesRegex(RuntimeError, "lineage write failed"):
                await runtime.create_lineage(workspace, uuid.uuid4().hex)

            self._assert_no_unclaimed_worktree(runtime, source, identity.repository_id)
            runtime.close()

    async def test_committed_lineage_is_not_deleted_when_acknowledgement_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            sessions = _CommittedThenFailedSessions()
            runtime = ManagedWorkspaceRuntime(sessions, root / "state")
            workspace = await runtime.prepare_task(source, "pending")

            with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
                await runtime.create_lineage(workspace, uuid.uuid4().hex)

            self.assertIsNotNone(sessions.record)
            self.assertTrue(workspace.worktree_root.exists())
            self.assertEqual(runtime._prepared, {})
            runtime.close()

    async def test_abort_waits_for_inflight_lineage_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            _init_git_source(source)
            sessions = _BlockingCommitSessions()
            runtime = ManagedWorkspaceRuntime(sessions, root / "state")
            workspace = await runtime.prepare_task(source, "pending")
            persist = asyncio.create_task(
                runtime.create_lineage(workspace, uuid.uuid4().hex)
            )
            await sessions.entered.wait()

            abort = asyncio.create_task(runtime.abort_prepared_task(workspace))
            await asyncio.sleep(0.05)
            self.assertFalse(abort.done())
            sessions.release.set()

            await persist
            self.assertFalse(await abort)
            self.assertIsNotNone(sessions.record)
            self.assertTrue(workspace.worktree_root.exists())
            self.assertEqual(runtime._prepared, {})
            runtime.close()

    def _assert_no_unclaimed_worktree(
        self,
        runtime: ManagedWorkspaceRuntime,
        source: Path,
        repository_id: str,
    ) -> None:
        repository_root = runtime._worktrees.storage_root / repository_id
        directories = (
            tuple(path for path in repository_root.iterdir() if path.is_dir())
            if repository_root.exists()
            else ()
        )
        self.assertEqual(directories, ())
        branches = subprocess.run(
            ("git", "branch", "--list", "codex/task-*"),
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(branches, "")
        self.assertEqual(runtime._prepared, {})


if __name__ == "__main__":
    unittest.main()
