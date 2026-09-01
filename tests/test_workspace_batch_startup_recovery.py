from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.workspace.edits import BatchApplyResult, BatchApplyStatus
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime
from code_agent_win.workspace_startup_recovery import (
    WorkspaceBatchRecoveryConflict,
    recover_workspace_edit_batches,
)


class _Sessions:
    async def list_tasks(self, *, include_terminal: bool = False):
        del include_terminal
        return ()

    async def pending_rewinds(self):
        return ()


class WorkspaceBatchStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.runtime = ManagedWorkspaceRuntime(
            _Sessions(), self.base / "managed-workspaces"
        )

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    async def test_startup_orders_batch_recovery_before_checkpoint_recovery(self) -> None:
        events: list[str] = []

        async def hydrate() -> None:
            events.append("hydrate")

        async def batches():
            events.append("batches")
            return ("batch",)

        async def checkpoints():
            events.append("checkpoints")
            return ("checkpoint",)

        self.runtime.hydrate_bindings = hydrate
        self.runtime.recover_edit_batches = batches
        self.runtime.recover_pending = checkpoints

        results = await self.runtime.startup()

        self.assertEqual(
            events, ["hydrate", "batches", "checkpoints", "hydrate"]
        )
        self.assertEqual(results, ("batch", "checkpoint"))

    async def test_concurrent_startup_runs_recovery_once(self) -> None:
        calls = 0
        started = asyncio.Event()
        finish = asyncio.Event()

        async def batches():
            nonlocal calls
            calls += 1
            started.set()
            await finish.wait()
            return ()

        async def no_op():
            return None

        async def no_pending():
            return ()

        self.runtime.hydrate_bindings = no_op
        self.runtime.recover_edit_batches = batches
        self.runtime.recover_pending = no_pending
        first = asyncio.create_task(self.runtime.startup())
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(self.runtime.startup())
        finish.set()

        await asyncio.gather(first, second)
        self.assertEqual(calls, 1)

    async def test_foreign_batch_blocks_later_roots_and_checkpoint_recovery(self) -> None:
        source = self.base / "source"
        task = self.base / "task"
        source.mkdir()
        task.mkdir()
        calls: list[Path] = []

        class Capture:
            def __init__(self, root: Path, status: BatchApplyStatus) -> None:
                self.root, self.status = root, status

            async def recover_edit_batches(self):
                calls.append(self.root)
                return (BatchApplyResult(self.status),)

        captures = {
            source: Capture(source, BatchApplyStatus.PARTIAL_CONFLICT),
            task: Capture(task, BatchApplyStatus.ROLLED_BACK),
        }

        class Runtime:
            _thread_roots = {}
            _task_roots = {"task": task}

            @staticmethod
            def services_for_root(root: Path):
                return SimpleNamespace(root=root)

        class Pool:
            @staticmethod
            def for_services(services):
                return SimpleNamespace(capture=captures[services.root])

        with self.assertRaises(WorkspaceBatchRecoveryConflict):
            await recover_workspace_edit_batches(Runtime(), Pool(), source)

        self.assertEqual(calls, [source])


if __name__ == "__main__":
    unittest.main()
