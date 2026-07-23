from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from code_agent.checkpoints.service import CheckpointService
from code_agent.core.cancellation import CancellationError
from code_agent.core.task import TaskStatus
from code_agent.interfaces.controller import AgentController
from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workflows.service import WorkflowService
from code_agent_win.foreground_tasks import IntegratedForegroundTaskController
from code_agent_win.workspace_checkpoint_runtime import _ListedCheckpointService
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, *args, cancellation, **kwargs):
        self.started.set()
        await cancellation.wait_async()
        raise CancellationError(cancellation.reason or "cancelled")
        yield None


class _Subagents:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.release_started = asyncio.Event()
        self.allow_release = asyncio.Event()

    def activate(self, task_id: str):
        return object()

    async def release(self, task_id: str) -> None:
        self.order.append("release-start")
        self.release_started.set()
        await self.allow_release.wait()
        self.order.append("release-done")

    def reset(self, token: object) -> None:
        self.order.append("reset")


class _CheckpointControl:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.labels: list[str] = []

    async def create(self, task_id: str, label: str):
        self.labels.append(label)
        self.order.append(f"capture:{label}")
        return SimpleNamespace(id=f"cp-{len(self.labels)}")


class _FailOnceCheckpointControl(_CheckpointControl):
    def __init__(self, order: list[str]) -> None:
        super().__init__(order)
        self.failed = False

    async def create(self, task_id: str, label: str):
        if not self.failed:
            self.failed = True
            raise RuntimeError("checkpoint unavailable")
        return await super().create(task_id, label)


def _subscribe_settled_callbacks(
    controller: IntegratedForegroundTaskController, order: list[str]
) -> None:
    def broken_callback() -> None:
        order.append("callback-broken")
        raise RuntimeError("reload failed")

    async def async_callback() -> None:
        order.append("callback-async")

    controller.subscribe_settled(broken_callback)
    controller.subscribe_settled(async_callback)


class WorkspaceQuiescerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_proxy_fails_closed_until_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            runtime = ManagedWorkspaceRuntime(sessions, root / "state")
            proxy = runtime.quiesce_task

            with self.assertRaisesRegex(RuntimeError, "not bound"):
                await proxy("task-1")

            calls: list[str] = []

            async def quiesce(task_id: str) -> None:
                calls.append(task_id)

            runtime.set_quiescer(quiesce)
            await proxy("task-1")

        self.assertEqual(calls, ["task-1"])

    async def test_unbound_proxy_prevents_workspace_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = ManagedWorkspaceRuntime(
                object(), root / "state"
            )
            sessions = SimpleNamespace(
                load_lineage_for_task=AsyncMock(
                    return_value=SimpleNamespace(id="lineage-1")
                )
            )
            workspace = SimpleNamespace(inventory=AsyncMock())
            service = CheckpointService(
                sessions, workspace, runtime.quiesce_task
            )

            with self.assertRaisesRegex(RuntimeError, "not bound"):
                await service.capture("task-1", "manual")

        workspace.inventory.assert_not_awaited()


class ForegroundBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_initial_checkpoint_does_not_leave_active_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            checkpoints = _FailOnceCheckpointControl([])
            controller = IntegratedForegroundTaskController(
                AgentController(_BlockingRunner()),
                sessions,
                root,
                subagents=_Subagents([]),
                workflows=WorkflowService(sessions),
                checkpoints=checkpoints,
            )

            with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
                await controller.start("first")
            failed = (await sessions.list_tasks())[0]
            replacement = await controller.start("second")

        self.assertEqual(failed.status, TaskStatus.INTERRUPTED)
        self.assertEqual(
            failed.stop_reason,
            "task creation failed before initial checkpoint",
        )
        self.assertEqual(replacement.status, TaskStatus.CREATED)

    async def test_first_yield_quiesce_waits_for_token_and_subagent_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            order: list[str] = []
            runner = _BlockingRunner()
            subagents = _Subagents(order)
            checkpoints = _CheckpointControl(order)
            controller = IntegratedForegroundTaskController(
                AgentController(runner),
                sessions,
                root,
                subagents=subagents,
                workflows=WorkflowService(sessions),
                checkpoints=checkpoints,
            )
            _subscribe_settled_callbacks(controller, order)
            task = await controller.start("wait safely")
            order.clear()
            checkpoints.labels.clear()
            stream = controller.events(task.id)

            first = await anext(stream)
            self.assertEqual(first.payload["status"], "running")
            self.assertNotIn(task.id, controller._tokens)

            pause = asyncio.create_task(controller.pause(task.id, "pause now"))
            await asyncio.sleep(0)
            draining = asyncio.create_task(_drain(stream))
            await runner.started.wait()
            await subagents.release_started.wait()

            self.assertFalse(pause.done())
            self.assertNotIn("capture:task-paused", order)
            subagents.allow_release.set()
            await asyncio.gather(pause, draining)

            stored = await sessions.load_task(task.id)

        self.assertEqual(stored.status, TaskStatus.PAUSED)
        self.assertEqual(checkpoints.labels, ["task-paused"])
        self.assertLess(order.index("release-done"), order.index("capture:task-paused"))
        self.assertLess(order.index("capture:task-paused"), order.index("callback-broken"))
        self.assertIn("callback-async", order)


class ListedCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_skips_legacy_records_without_checkpoint_cursor(self) -> None:
        now = datetime.now(timezone.utc)
        legacy = CheckpointRecord("legacy", "thread-1", "task-paused", {}, now)
        workspace = CheckpointRecord(
            "workspace", "thread-1", "durable", {}, now
        )

        class Sessions:
            async def load_task(self, task_id: str):
                return SimpleNamespace(thread_id="thread-1")

            async def list_checkpoints(self, thread_id: str):
                return (legacy, workspace)

            async def load_checkpoint_cursor(self, checkpoint_id: str):
                if checkpoint_id == "legacy":
                    raise SessionNotFound("cursor not found")
                return object()

        async def quiesce(task_id: str) -> None:
            return None

        service = _ListedCheckpointService(Sessions(), object(), quiesce)

        records = await service.list("task-1")

        self.assertEqual(records, (workspace,))


async def _drain(stream: object) -> list[object]:
    return [event async for event in stream]


if __name__ == "__main__":
    unittest.main()
