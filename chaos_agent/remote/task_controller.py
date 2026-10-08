from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from code_agent.core.task import TaskStatus
from code_agent.core.task_result import ResultCollector, TaskResult, result_from_task
from code_agent.project_launcher.store import ProjectStore

from .applications import RemoteApplications
from .catalog import RemoteCatalog
from .errors import RemoteConflict, RemoteNotFound
from .protocol import MobileEvent, event_from_agent


_TERMINAL = {"completed", "accepted_partial", "failed", "superseded"}
_TRANSCRIPT_BYTES = 1024 * 1024


class _HistoryChanged(RuntimeError):
    """Retry a read-only snapshot whose pinned run changed across an await."""


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
    result: dict[str, Any] | None = None


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
        self.host_epoch = uuid.uuid4().hex

    async def request_task(self, session_id: str):
        """Resolve the exact persistent task selected by a catalog session."""
        if session_id == 'current':
            if self._active is None:
                raise RemoteNotFound('no current task')
            session_id = self._active.session_id
        snapshot = await self.catalog.snapshot()
        snapshot.session(session_id)
        task = snapshot.tasks.get(session_id)
        if task is None:
            raise RemoteNotFound('session has no task')
        return task

    async def request_application(self, task_id: str):
        """Use a persistent project identity without closing another active app."""
        snapshot = await self.catalog.snapshot()
        task = next((item for item in snapshot.tasks.values() if item.id == task_id), None)
        if task is None:
            raise RemoteNotFound('unknown task')
        row = snapshot.session(task.thread_id)
        project = snapshot.project(row['project_id'])
        self.catalog.require_available(project)
        for run in (self._active, self._runs.get(task.thread_id)):
            if run is not None and run.task_id == task.id and (
                    run.application is self.applications.primary or run.application is self.applications.child):
                return run.application, task
        active = self._active
        if active is not None and active.runner is not None and not active.runner.done():
            raise RemoteConflict('another task is executing; wait before opening this project')
        return await self.applications.for_root(Path(project['path'])), task

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
            return {"status": "idle", "task_id": None, "session_id": None, "sequence": 0, "host_epoch": self.host_epoch}
        await self._reconcile_finished_run(run)
        if self._active is not run:
            return await self.status()
        return {"status": run.status, "task_id": run.task_id, "session_id": run.session_id, "sequence": run.sequence, "result": run.result, "host_epoch": self.host_epoch}

    async def _reconcile_finished_run(self, run: _RemoteRun) -> None:
        """Refresh a finished stream from its exact durable task, never a new run."""
        sessions = getattr(run.application, 'sessions', None)
        if not run.stream_done or sessions is None or self._runs.get(run.session_id) is not run:
            return
        task = await sessions.load_task(run.task_id)
        if task.thread_id != run.session_id or task.status.value in {'created', 'running', 'verifying'}:
            return
        load_result = getattr(run.application.foreground_tasks, 'result', None)
        result = await load_result(run.task_id) if callable(load_result) else result_from_task(task)
        current = await sessions.load_task(run.task_id)
        if (current.updated_at != task.updated_at or not run.stream_done
                or self._runs.get(run.session_id) is not run):
            return
        # A result write may have failed after the durable decision transition.
        # Report that task's execution fact without promoting stale verification.
        if result.execution_status != task.status.value:
            result = result_from_task(task)
        run.status, run.result = result.execution_status, result.to_dict()

    async def history(self, session_id: str, *, before: int | None, limit: int) -> dict[str, Any]:
        for _ in range(3):
            try:
                return await self._history_snapshot(session_id, before=before, limit=limit)
            except _HistoryChanged:
                continue
        raise RemoteConflict('session changed during history snapshot; retry')

    def _check_history_run(self, session_id, run):
        if self._runs.get(session_id) is not run:
            raise _HistoryChanged()

    async def _history_snapshot(self, session_id: str, *, before: int | None, limit: int) -> dict[str, Any]:
        run = self._runs.get(session_id)
        snapshot = await self.catalog.snapshot()
        self._check_history_run(session_id, run)
        row = snapshot.session(session_id)
        task = snapshot.tasks.get(session_id)
        if run is not None:
            await self._reconcile_finished_run(run)
            self._check_history_run(session_id, run)
        live = run is not None and not run.stream_done and run.status not in _TERMINAL | {"stopped"}
        if live:
            task = await self.catalog.sessions.load_task(run.task_id)
            self._check_history_run(session_id, run)
            if task.thread_id != session_id:
                raise RemoteConflict('history run does not match its task session')
            if row['status'] != 'archived':
                row['status'] = task.status.value
        maximum = run.base_message_sequence if live else None
        page, next_before = await self.catalog.message_page(session_id, before=before, limit=limit, maximum=maximum)
        self._check_history_run(session_id, run)
        if not live:
            task = await self.catalog.sessions.load_task_for_thread(session_id)
            self._check_history_run(session_id, run)
            if task is not None and row["status"] != "archived":
                row["status"] = task.status.value
        result = {
            "host_epoch": self.host_epoch,
            "messages": page, "next_before": next_before, "session": row,
            "task": {"id": task.id, "status": task.status.value} if task is not None else None,
            "event_sequence": run.sequence if run is not None else 0,
            "active_prompt": None, "active_task": None, "assistant_open": False,
            "snapshot_truncated": False,
            "result": None,
        }
        if live and run is not None:
            async with run.condition:
                self._check_history_run(session_id, run)
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
                self._check_history_run(session_id, run)
                result["messages"], result["next_before"] = page, next_before
                task = await self.catalog.sessions.load_task_for_thread(session_id)
                self._check_history_run(session_id, run)
                if task is not None:
                    result["task"] = {"id": task.id, "status": task.status.value}
                    result["session"]["status"] = task.status.value
        if task is not None and not live and task.status.value not in {'created', 'running', 'verifying'}:
            load_result = getattr(self._application.foreground_tasks, 'result', None)
            if callable(load_result):
                # This public projection reads the shared repository; no project
                # application is created or swapped merely to view history.
                durable = await load_result(task.id)
                self._check_history_run(session_id, run)
                current = await self.catalog.sessions.load_task(task.id)
                self._check_history_run(session_id, run)
                if current.updated_at != task.updated_at:
                    raise _HistoryChanged()
                result['result'] = durable.to_dict()
        return result

    async def events(self, since: int = 0, session_id: str = "current", *, host_epoch: str | None = None) -> AsyncIterator[MobileEvent]:
        # Resolve the alias once. A later task can never replace this stream.
        run = self._active if session_id == "current" else self._runs.get(session_id)
        if run is None:
            return
        if host_epoch is not None and host_epoch != self.host_epoch:
            yield self._gap(run, "host_restarted")
            return
        next_sequence = max(1, since + 1)
        while True:
            async with run.condition:
                gap = (next_sequence > run.sequence + 1 or
                       bool(run.events and next_sequence < run.events[0].sequence))
                pending = [event for event in run.events if event.sequence >= next_sequence]
                if not gap and not pending and run.stream_done:
                    return
                if not gap and not pending:
                    await run.condition.wait()
                    continue
            if gap:
                yield self._gap(run, "cursor_gap")
                return
            for event in pending:
                next_sequence = event.sequence + 1
                yield event

    def _gap(self, run: _RemoteRun, reason: str) -> MobileEvent:
        return MobileEvent("connection_state", task_id=run.task_id, session_id=run.session_id,
            sequence=run.sequence, data={"reset": True, "snapshot_required": True,
                                        "reason": reason, "host_epoch": self.host_epoch})

    async def _consume(self, run: _RemoteRun, instruction: str | None) -> None:
        run.status = "running"
        collected = ResultCollector()
        try:
            foreground = run.application.foreground_tasks
            stream = foreground.events(run.task_id) if instruction is None else foreground.events(run.task_id, instruction)
            async for event in stream:
                collected.observe(event)
                mobile = event_from_agent(event, task_id=run.task_id, session_id=run.session_id, sequence=run.sequence + 1)
                if mobile is not None:
                    if mobile.event in {'task_completed', 'task_stopped'}:
                        pass  # Final delivery follows the durable result lookup.
                    else:
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
        finally:
            result = collected.result
            sessions = getattr(run.application, 'sessions', None)
            if sessions is not None:
                try:
                    load_result = getattr(run.application.foreground_tasks, 'result', None)
                    if callable(load_result):
                        result = await load_result(run.task_id)
                    else:
                        result = result_from_task(await sessions.load_task(run.task_id))
                except Exception:
                    result = TaskResult(stop_code='state_read_failed')
            elif run.status == 'failed':
                result = TaskResult('failed', stop_code='execution_error')
            if result.stop_code != 'state_read_failed':
                result = collected.reconcile(result)
            run.status = result.execution_status
            run.result = result.to_dict()
            terminal_name = {'completed': 'task_completed', 'failed': 'task_failed',
                             'cancelled': 'task_stopped'}.get(result.execution_status)
            await self._publish(run, MobileEvent(terminal_name or 'task_status', task_id=run.task_id,
                session_id=run.session_id, data={'status': run.status, 'result': result.to_dict()}))
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
                result_status = ((event.data or {}).get('result') or {}).get('execution_status')
                run.status = result_status or event.event.removeprefix("task_")
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
