"""ACP session projection over the single persistent foreground task service."""
from pathlib import Path

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskStatus
from code_agent.sessions.errors import SessionNotFound
from chaos_agent.foreground_task_support import same_path


_LABEL = "acp-session-selection"


class AcpTaskService:
    """Keep protocol session IDs stable without owning an execution lifecycle."""

    def __init__(self, tasks, sessions, root):
        self.tasks, self.sessions, self.root = tasks, sessions, Path(root).resolve()

    async def create_thread(self):
        thread = await self.sessions.create_thread()
        await self._save(thread, thread, None)
        return thread

    async def _save(self, session, thread, task):
        await self.sessions.create_checkpoint(session, _LABEL, {
            "session_id": session, "thread_id": thread, "task_id": task,
            "source_root": str(self.root),
        })

    async def _source_root(self, task):
        try:
            lineage = await self.sessions.load_lineage_for_task(task.id)
            return Path(lineage.source_root)
        except SessionNotFound:
            return Path(task.contract.authorization.workspace_root)

    async def selection(self, session):
        checkpoints = await self.sessions.list_checkpoints(session)
        selected = [item for item in checkpoints if item.label == _LABEL]
        if selected:
            data = selected[-1].metadata
            if (data.get("session_id") != session or not isinstance(data.get("thread_id"), str)
                    or not isinstance(data.get("source_root"), str)
                    or not same_path(Path(data["source_root"]), self.root)):
                raise ValueError("invalid ACP session projection")
            thread = data["thread_id"]
        else:
            thread = session
            data = None
        relation = await self.sessions.load_thread_relation(thread)
        session_relation = await self.sessions.load_thread_relation(session)
        if session_relation.parent_thread_id is not None:
            raise ValueError("ACP session must be a root conversation")
        if thread != session and relation.parent_thread_id != session:
            raise ValueError("ACP selection is outside its conversation")
        if await self.sessions.context_records(thread, "child_budget"):
            raise ValueError("child execution cannot become an ACP session")
        task = await self.sessions.load_task_for_thread(thread)
        if task is not None:
            if data is not None and data.get("task_id") != task.id:
                raise ValueError("ACP task selection is stale")
            if not same_path(await self._source_root(task), self.root):
                raise ValueError("ACP task belongs to another project")
        elif data is None or data.get("task_id") is not None:
            raise ValueError("legacy conversation has no trusted project provenance")
        return thread, task

    async def load_messages(self, session):
        thread, _ = await self.selection(session)
        return await self.sessions.load_messages(thread)

    async def list_threads(self, *, limit=1000):
        result = []
        for row in await self.sessions.list_threads(limit=limit):
            try:
                await self.selection(row.id)
            except (ValueError, SessionNotFound):
                continue
            result.append(row)
        return tuple(result)

    async def ask(self, prompt, *, thread_id, cancellation):
        thread, task = await self.selection(thread_id)
        if task is None:
            stats = getattr(self.sessions, "history_stats", None)
            occupied = ((await stats(thread))["message_count"] if callable(stats)
                        else bool(await self.sessions.load_messages(thread)))
            task = await self.tasks.start(prompt, **(
                {"source_thread_id": thread} if occupied else {"thread_id": thread}))
        elif task.status.is_terminal:
            task = await self.tasks.start(prompt, source_thread_id=thread)
        try:
            await self._save(thread_id, task.thread_id, task.id)
        except BaseException:
            if task.status is TaskStatus.CREATED:
                await self.sessions.transition_task(task.id, TaskStatus.INTERRUPTED,
                                                    "ACP session projection setup failed")
            raise
        async for event in self.tasks.events(task.id, prompt, cancellation=cancellation):
            yield event
        result = await self.tasks.result(task.id)
        yield AgentEvent(EventKind.TASK_RESULT, {"task_id": task.id, "result": result.to_dict()})
