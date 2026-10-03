from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.project_launcher.store import ProjectStore
from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote.server import create_host_app


class Foreground:
    def __init__(self, sessions, root):
        self.sessions, self.root = sessions, root
        self.started, self.restored = [], []
        self.hold = False
        self.release = None

    async def start(self, prompt, *, thread_id=None, source_thread_id=None):
        self.started.append((prompt, thread_id, source_thread_id))
        if source_thread_id:
            thread_id = await self.sessions.create_thread_from_history(source_thread_id)
        if thread_id is None:
            thread_id = await self.sessions.create_thread()
        return await self.sessions.create_task(thread_id, TaskContract(prompt, TaskAuthorization.local_workspace(str(self.root))))

    async def restore_runtime_settings(self, identifier):
        self.restored.append(identifier)

    async def events(self, identifier, prompt=None):
        task = await self.sessions.load_task(identifier)
        await self.sessions.transition_task(identifier, TaskStatus.RUNNING)
        await self.sessions.append_message(task.thread_id, Message("user", prompt or task.contract.objective))
        yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"status": "running"})
        yield AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="live answer").to_dict()})
        if self.hold:
            self.release = asyncio.Event()
            await self.release.wait()
        await self.sessions.append_message(task.thread_id, Message("assistant", "live answer"))
        await self.sessions.transition_task(identifier, TaskStatus.COMPLETED)
        yield AgentEvent(EventKind.COMPLETED)

    async def interrupt(self, identifier, reason):
        task = await self.sessions.load_task(identifier)
        if task.status in {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            await self.sessions.transition_task(identifier, TaskStatus.INTERRUPTED, reason)


class NavigationFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "project-a"
        self.other = self.directory / "project-b"
        self.root.mkdir()
        self.other.mkdir()
        self.database = self.directory / "sessions.sqlite3"
        self.project_store = ProjectStore(self.directory / "projects.json")
        self.sessions = SQLiteSessionRepository(self.database)
        self.foreground = Foreground(self.sessions, self.root)
        self.bindings = []
        self.application = SimpleNamespace(
            workspace_root=self.root, sessions=self.sessions,
            project_store=self.project_store,
            foreground_tasks=self.foreground,
            workspace_runtime=SimpleNamespace(bind_thread=lambda identifier, root: self.bindings.append((identifier, root))),
        )
        self.children, self.factory_roots = [], []
        self.pairing = PairingStore(self.directory / "devices.json")
        self.headers = {"authorization": "Bearer " + self.pairing.pair(self.pairing.issue_token())}
        self.app, _ = create_host_app(self.application, pairing=self.pairing, application_factory=self.factory)

    def factory(self, root):
        self.factory_roots.append(root)
        child = SimpleNamespace(workspace_root=root, sessions=self.sessions, foreground_tasks=Foreground(self.sessions, root), closed=False)
        async def close():
            child.closed = True
        child.aclose = close
        self.children.append(child)
        return child

    def seed(self, root=None, *, title=None, status=TaskStatus.COMPLETED, messages=()):
        async def create():
            identifier = await self.sessions.create_thread(title)
            for role, content in messages:
                await self.sessions.append_message(identifier, Message(role, content))
            if root is not None:
                task = await self.sessions.create_task(identifier, TaskContract(title or "old task", TaskAuthorization.local_workspace(str(root))))
                if status is TaskStatus.PAUSED:
                    await self.sessions.transition_task(task.id, status)
                else:
                    await self.sessions.transition_task(task.id, TaskStatus.RUNNING)
                    if status is not TaskStatus.RUNNING:
                        await self.sessions.transition_task(task.id, status)
            return identifier
        return asyncio.run(create())

    def project(self, client, root):
        projects = client.get("/projects", headers=self.headers).json()["projects"]
        return next(item for item in projects if item["path"] == str(root))

    def empty(self, client, root):
        identifier = self.project(client, root)["id"]
        return client.post("/sessions", headers=self.headers, json={"project_id": identifier}).json()["session_id"]
