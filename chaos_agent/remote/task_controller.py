from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from code_agent.core.task import TaskStatus
from code_agent.project_launcher.store import ProjectStore

from .applications import RemoteApplications
from .catalog import RemoteCatalog
from .errors import RemoteConflict, RemoteNotFound
from .protocol import MobileEvent, event_from_agent


_TERMINAL = {"completed", "accepted_partial", "failed", "superseded"}
_TRANSCRIPT_BYTES = 1024 * 1024


@dataclass
class _RemoteRun:
    task_id: str
    session_id: str
    application: object
    prompt: str
    events: deque[MobileEvent] = field(default_factory=lambda: deque(maxlen=256))
    sequence: int = 0
    status: str = "created"
    base_message_sequence: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    runner: asyncio.Task[None] | None = None
    stream_done: bool = False
    transcript: list[dict[str, Any]] = field(default_factory=list)
    transcript_bytes: int = 0
    assistant_open: bool = False
    snapshot_truncated: bool = False


class RemoteTaskController:
    """Own one Host execution slot and pin each event stream to its session."""

    def __init__(self, application: object, *, application_factory: Callable[[Path], Any] | None = None, project_store: ProjectStore | None = None) -> None:
        self._application = application
        self.catalog = RemoteCatalog(application, project_store)
        self.applications = RemoteApplications(application, application_factory)
        self._active: _RemoteRun | None = None
        self._runs: dict[str, _RemoteRun] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self, prompt: str, session_id: str = "current") -> dict[str, str]:
        async with self._lock:
            if self._closed:
                raise RemoteConflict("Host is closing")
            if self._active is not None and self._active.runner is not None and not self._active.runner.done():
                raise RemoteConflict("a remote task is already active")
            application, task, continued_from, instruction = await self._prepare(prompt, session_id)
            run = _RemoteRun(task.id, task.thread_id, application, prompt)
            sessions = getattr(application, "sessions", None)
            if sessions is not None:
                try:
                    records = await sessions.load_message_records(task.thread_id, limit=1)
                except BaseException:
                    if getattr(task, "status", None) is TaskStatus.CREATED:
                        try:
                            await sessions.transition_task(task.id, TaskStatus.INTERRUPTED, "remote event setup failed")
                        except Exception:
                            pass
                    raise
                run.base_message_sequence = records[-1].sequence if records else 0
            self._append_text(run, "user", prompt)
            self._active = run
            self._runs[run.session_id] = run
            while len(self._runs) > 32:
                del self._runs[next(iter(self._runs))]
            await self._publish(run, MobileEvent("task_started", task_id=task.id, session_id=task.thread_id))
            run.runner = asyncio.create_task(self._consume(run, instruction), name=f"remote-task-{task.id}")
            result = {"task_id": task.id, "session_id": task.thread_id}
            if continued_from is not None:
                result["continued_from"] = continued_from
            return result

    async def _prepare(self, prompt: str, session_id: str) -> tuple[object, Any, str | None, str | None]:
        if session_id == "current":
            if self.catalog.sessions is not None:
                snapshot = await self.catalog.snapshot()
                self._ensure_idle_project(snapshot, snapshot.current_project_id)
            return self._application, await self._application.foreground_tasks.start(prompt), None, None
        snapshot = await self.catalog.snapshot()
        row = snapshot.session(session_id)
        project = snapshot.project(row["project_id"])
        self.catalog.require_available(project)
        previous = snapshot.tasks.get(session_id)
        state = getattr(getattr(previous, "status", None), "value", None)
        if state in {"created", "running", "verifying"}:
            raise RemoteConflict("this session is already executing")
        self._ensure_idle_project(snapshot, row["project_id"])
        application = await self.applications.for_root(Path(project["path"]))
        foreground = application.foreground_tasks
        if previous is not None:
            if state not in _TERMINAL:
                await foreground.restore_runtime_settings(previous.id)
                return application, previous, None, prompt
        if previous is None and row["message_count"] == 0:
            task = await foreground.start(prompt, thread_id=session_id)
            return application, task, None, None
        task = await foreground.start(prompt, source_thread_id=session_id)
        return application, task, session_id, None

    @staticmethod
    def _ensure_idle_project(snapshot: Any, identifier: str | None) -> None:
        if any(
            item["project_id"] == identifier
            and item["id"] in snapshot.tasks
            and snapshot.tasks[item["id"]].status.value in {"created", "running", "verifying"}
            for item in snapshot.sessions
        ):
            raise RemoteConflict("another task is already executing in this project")

    async def stop(self, task_id: str) -> None:
        run = self._require(task_id)
        if run.stream_done or run.status in _TERMINAL | {"stopped"}:
            raise RemoteConflict("task is no longer active")
        await run.application.foreground_tasks.interrupt(task_id, "remote user stopped task")
        run.status = "stopped"
        await self._publish(run, MobileEvent("task_stopped", task_id=run.task_id, session_id=run.session_id))

    async def status(self) -> dict[str, Any]:
        run = self._active
        if run is None:
            return {"status": "idle", "task_id": None, "session_id": None, "sequence": 0}
        return {"status": run.status, "task_id": run.task_id, "session_id": run.session_id, "sequence": run.sequence}

    async def history(self, session_id: str, *, before: int | None, limit: int) -> dict[str, Any]:
        snapshot = await self.catalog.snapshot()
        row = snapshot.session(session_id)
        task = snapshot.tasks.get(session_id)
        run = self._runs.get(session_id)
        live = run is not None and not run.stream_done and run.status not in _TERMINAL | {"stopped"}
        maximum = run.base_message_sequence if live else None
        page, next_before = await self.catalog.message_page(session_id, before=before, limit=limit, maximum=maximum)
        if not live:
            task = await self.catalog.sessions.load_task_for_thread(session_id)
            if task is not None and row["status"] != "archived":
                row["status"] = task.status.value
        result = {
            "messages": page, "next_before": next_before, "session": row,
            "task": {"id": task.id, "status": task.status.value} if task is not None else None,
            "event_sequence": run.sequence if run is not None else 0,
            "active_prompt": None, "active_task": None, "assistant_open": False,
            "snapshot_truncated": False,
        }
        if live and run is not None:
            async with run.condition:
                result["event_sequence"] = run.sequence
                live = not run.stream_done and run.status not in _TERMINAL | {"stopped"}
                if live:
                    if before is None:
                        result["messages"] = page + [dict(item) for item in run.transcript]
                        result["assistant_open"] = run.assistant_open
                    result["active_prompt"] = run.prompt
                    result["active_task"] = {"id": run.task_id, "session_id": run.session_id, "status": run.status}
                    result["snapshot_truncated"] = run.snapshot_truncated
            if not live:
                # Completion may have arrived while the base page was loading.
                page, next_before = await self.catalog.message_page(session_id, before=before, limit=limit)
                result["messages"], result["next_before"] = page, next_before
                task = await self.catalog.sessions.load_task_for_thread(session_id)
                if task is not None:
                    result["task"] = {"id": task.id, "status": task.status.value}
                    result["session"]["status"] = task.status.value
        return result

    async def events(self, since: int = 0, session_id: str = "current") -> AsyncIterator[MobileEvent]:
        # Resolve the alias once. A later task can never replace this stream.
        run = self._active if session_id == "current" else self._runs.get(session_id)
        if run is None:
            return
        next_sequence = max(1, since + 1)
        while True:
            async with run.condition:
                pending = [event for event in run.events if event.sequence >= next_sequence]
                if not pending and run.stream_done:
                    return
                if not pending:
                    await run.condition.wait()
                    continue
            for event in pending:
                next_sequence = event.sequence + 1
                yield event

    async def _consume(self, run: _RemoteRun, instruction: str | None) -> None:
        run.status = "running"
        try:
            foreground = run.application.foreground_tasks
            stream = foreground.events(run.task_id) if instruction is None else foreground.events(run.task_id, instruction)
            async for event in stream:
                mobile = event_from_agent(event, task_id=run.task_id, session_id=run.session_id, sequence=run.sequence + 1)
                if mobile is not None:
                    await self._publish(run, mobile)
        except asyncio.CancelledError:
            raise
        except Exception:
            run.status = "failed"
            sessions = getattr(run.application, "sessions", None)
            if sessions is not None:
                try:
                    task = await sessions.load_task(run.task_id)
                    if task.status in {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.VERIFYING}:
                        await run.application.foreground_tasks.interrupt(run.task_id, "remote event stream failed")
                except Exception:
                    pass
            await self._publish(run, MobileEvent("task_failed", task_id=run.task_id, session_id=run.session_id, data={"error": "task execution failed"}))
        finally:
            if run.status not in _TERMINAL | {"stopped"}:
                sessions = getattr(run.application, "sessions", None)
                if sessions is not None:
                    try:
                        with_task = await sessions.load_task(run.task_id)
                        run.status = with_task.status.value
                    except Exception:
                        run.status = "failed"
            async with run.condition:
                run.stream_done = True
                run.assistant_open = False
                run.condition.notify_all()

    async def _publish(self, run: _RemoteRun, event: MobileEvent) -> None:
        async with run.condition:
            if event.sequence <= run.sequence:
                event = MobileEvent(event.event, event.task_id, event.session_id, run.sequence + 1, event.data)
            run.sequence = event.sequence
            run.events.append(event)
            if event.event == "assistant_delta":
                self._append_text(run, "assistant", str((event.data or {}).get("text", "")))
            elif event.event in {"tool_started", "task_started", "task_completed", "task_failed", "task_stopped"}:
                run.assistant_open = False
            if event.event == "task_status":
                run.status = str((event.data or {}).get("status", run.status))
            elif event.event in {"task_completed", "task_failed", "task_stopped"}:
                run.status = event.event.removeprefix("task_")
            run.condition.notify_all()

    @staticmethod
    def _append_text(run: _RemoteRun, role: str, content: str) -> None:
        raw = content.encode("utf-8")
        remaining = max(0, _TRANSCRIPT_BYTES - run.transcript_bytes)
        if len(raw) > remaining:
            run.snapshot_truncated = True
            content = raw[:remaining].decode("utf-8", errors="ignore")
        if not content:
            return
        run.transcript_bytes += len(content.encode("utf-8"))
        if role == "assistant" and run.assistant_open:
            run.transcript[-1]["content"] += content
        else:
            run.transcript.append({"sequence": None, "role": role, "content": content})
        run.assistant_open = role == "assistant"

    def _require(self, task_id: str) -> _RemoteRun:
        run = self._active
        if run is None or run.task_id != task_id:
            raise RemoteNotFound("unknown task")
        return run

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            run = self._active
            try:
                if run is not None and run.runner is not None and not run.runner.done():
                    try:
                        await run.application.foreground_tasks.interrupt(run.task_id, "remote Host closed")
                    except ValueError:
                        # The task may have committed its terminal state during shutdown.
                        pass
                    finally:
                        run.runner.cancel()
                        await asyncio.gather(run.runner, return_exceptions=True)
            finally:
                await self.applications.aclose()
