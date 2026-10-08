import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from code_agent.core.events import AgentEvent, EventKind
from code_agent.project_launcher.store import ProjectStore
from chaos_agent.remote.task_controller import RemoteTaskController


class Foreground:
    async def start(self, prompt):
        return SimpleNamespace(id="gap-task", thread_id="gap-session")

    async def events(self, task_id):
        for index in range(300):
            yield AgentEvent(EventKind.ACTION_COMPLETED,
                {"result": {"name": "read_file", "request_id": str(index), "is_error": False}})
        yield AgentEvent(EventKind.COMPLETED)


class EventGapTests(unittest.IsolatedAsyncioTestCase):
    async def test_evicted_and_future_cursors_require_snapshot_before_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ProjectStore(root / "projects.json")
            controller = RemoteTaskController(SimpleNamespace(workspace_root=root,
                project_store=store, foreground_tasks=Foreground()), project_store=store)
            await controller.start("offline cursor test")
            await controller._active.runner
            status = await controller.status()
            for cursor in (0, status["sequence"] + 10):
                events = [event async for event in controller.events(cursor, "gap-session")]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].event, "connection_state")
                self.assertTrue(events[0].data["snapshot_required"])
                self.assertEqual(events[0].data["reason"], "cursor_gap")
            self.assertEqual([event async for event in controller.events(status["sequence"], "gap-session")], [])
            await controller.aclose()

    async def test_previous_host_epoch_never_replays_same_task_cursor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ProjectStore(root / "projects.json")
            application = SimpleNamespace(workspace_root=root, project_store=store, foreground_tasks=Foreground())
            previous = RemoteTaskController(application, project_store=store)
            controller = RemoteTaskController(application, project_store=store)
            self.assertNotEqual(previous.host_epoch, controller.host_epoch)
            await controller.start("restart cursor test")
            await controller._active.runner
            events = [event async for event in controller.events(300, "gap-session", host_epoch=previous.host_epoch)]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].data["reason"], "host_restarted")
            self.assertEqual(events[0].data["host_epoch"], controller.host_epoch)
            await controller.aclose()
            await previous.aclose()
