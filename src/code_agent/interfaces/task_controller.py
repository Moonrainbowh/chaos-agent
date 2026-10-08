from __future__ import annotations

import os
import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence, Mapping
from pathlib import Path

import psutil

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.attachments import AttachmentRef, freeze_attachments
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus
from code_agent.core.models import Message
from code_agent.core.task_intent import infer_task_intent

from .controller import AgentController


class ForegroundTaskController:
    """Own foreground cancellation and task lifecycle, leaving execution to AgentEngine."""

    def __init__(self, controller: AgentController, sessions: object, workspace_root: Path | str, *, profile_supplier: Callable[[], tuple[str, ...]] | None = None, profile_resolver: Callable[[str], Awaitable[None]] | None = None, runtime_resolver: Callable[[TaskContract], Awaitable[None]] | None = None, task_mode_supplier: Callable[[], str] | None = None) -> None:
        self._controller = controller
        self._sessions = sessions
        self._root = str(Path(workspace_root).resolve())
        self._tokens: dict[str, CancellationToken] = {}
        self._profile_supplier, self._profile_resolver = profile_supplier, profile_resolver
        self._runtime_resolver = runtime_resolver
        self._task_mode_supplier = task_mode_supplier

    async def start(self, prompt: str) -> TaskRecord:
        active = await self._sessions.list_tasks()
        if any(
            task.status in {TaskStatus.CREATED, TaskStatus.RUNNING}
            and os.path.normcase(task.contract.authorization.workspace_root) == os.path.normcase(self._root)
            for task in active
        ):
            raise RuntimeError("a foreground task is already active")
        thread_id = await self._sessions.create_thread()
        profile = self._profile_supplier() if self._profile_supplier else None
        selected_mode = self._task_mode_supplier() if self._task_mode_supplier else "code"
        interaction_mode = resolved_task_mode(prompt, selected_mode)
        task = await self._sessions.create_task(
            thread_id,
            freeze_task_contract(
                prompt, authorization_for_task_mode(self._root, interaction_mode), profile,
                interaction_mode=interaction_mode,
            ),
        )
        await self._sessions.create_checkpoint(thread_id, "task-created", {"task_id": task.id, "status": task.status.value})
        return task

    async def list(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]:
        return await self._sessions.list_tasks(include_terminal=include_terminal)

    async def recovery_checklist(self, task_id: str) -> dict[str, object]:
        """Read the durable recovery facts without invoking the model or tools."""
        checklist = getattr(self._sessions, "recovery_checklist", None)
        if not callable(checklist):
            raise RuntimeError("recovery checklist is unavailable")
        return await checklist(task_id)

    async def resolve_pending_action(self, task_id: str, **decision) -> AgentEvent:
        """Operator-only reconciliation; never invokes the runner or ordinary action policy."""
        task = await self._sessions.load_task(task_id)
        if os.path.normcase(os.path.realpath(task.contract.authorization.workspace_root)) != os.path.normcase(os.path.realpath(self._root)):
            raise ValueError("task belongs to a different workspace")
        if decision.get("decision") == "durable_receipt":
            from code_agent.core.action_semantics import READ_ONLY_TOOLS
            facts = await self.recovery_checklist(task_id)
            pending = [item for item in facts['pending_action_records'] if item['tool_call_id'] == decision.get('call_id')]
            if len(pending) != 1 or pending[0]['tool_name'] not in READ_ONLY_TOOLS:
                raise ValueError("side-effecting or opaque action requires Host mutation verification or an explicit operator report")
        if decision.get("decision") == "local_mutation":
            raise ValueError("local mutation reconciliation requires Host workspace verification")
        return await self._sessions.resolve_pending_action(task_id, workspace_root=self._root,
            owner_alive=_reconciliation_owner_alive, **decision)

    async def restore_runtime_settings(self, task_id: str) -> None:
        """Select a saved task's frozen runtime before displaying its conversation."""
        task = await self._sessions.load_task(task_id)
        if task.contract.runtime_selection_digest and self._runtime_resolver:
            await self._runtime_resolver(task.contract)
        elif task.contract.profile_id and self._profile_resolver:
            await self._profile_resolver(task.contract.profile_id)

    async def events(
        self,
        task_id: str,
        prompt: str | None = None,
        *,
        attachments: Sequence[AttachmentRef] = (),
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AgentEvent]:
        task = await self._sessions.load_task(task_id)
        checklist = getattr(self._sessions, "recovery_checklist", None)
        if callable(checklist):
            facts = await checklist(task.id)
            if facts.get("execution_owner") is not None:
                raise ValueError("task still has an execution owner; settle or reconcile it before resuming")
            unknown = facts.get("unresolved_tool_calls", ())
            if unknown:
                waiting = task if task.status is TaskStatus.WAITING_DECISION or task.status.is_terminal or task.status is TaskStatus.CREATED else await self._sessions.transition_task(
                    task.id, TaskStatus.WAITING_DECISION, "an interrupted tool operation has unknown outcome")
                event = AgentEvent(EventKind.TASK_DECISION_REQUIRED, {"task_id": waiting.id,
                    "status": waiting.status.value, "reason": "an interrupted tool operation has unknown outcome",
                    "unknown_tool_calls": unknown})
                await self._sessions.append_event(waiting.thread_id, event)
                yield event
                return
        if task.contract.runtime_selection_digest and self._runtime_resolver:
            try:
                await self._runtime_resolver(task.contract)
            except (RuntimeError, ValueError):
                waiting = task if task.status is TaskStatus.WAITING_DECISION else await self._sessions.transition_task(task.id, TaskStatus.WAITING_DECISION, "recorded runtime selection is unavailable")
                event = AgentEvent(EventKind.TASK_DECISION_REQUIRED, {"task_id": waiting.id, "status": waiting.status.value, "reason": "recorded runtime selection is unavailable"})
                await self._sessions.append_event(waiting.thread_id, event); yield event; return
        elif task.contract.profile_id and self._profile_resolver:
            try: await self._profile_resolver(task.contract.profile_id)
            except (RuntimeError, ValueError):
                waiting = task if task.status is TaskStatus.WAITING_DECISION else await self._sessions.transition_task(task.id, TaskStatus.WAITING_DECISION, "recorded model profile is unavailable")
                event = AgentEvent(EventKind.TASK_DECISION_REQUIRED, {"task_id": waiting.id, "status": waiting.status.value, "reason": "recorded model profile is unavailable"})
                await self._sessions.append_event(waiting.thread_id, event); yield event; return
        if task.status is TaskStatus.INTERRUPTED:
            interrupt_runs = getattr(self._sessions, "interrupt_open_verification_runs", None)
            if callable(interrupt_runs):
                await interrupt_runs(task.id)
        register = getattr(self._sessions, "register_task_execution", None)
        begin = getattr(self._sessions, "begin_task_execution", None)
        instance_id = uuid.uuid4().hex
        if callable(begin):
            process = psutil.Process(os.getpid())
            task = await begin(task.id, instance_id, process.pid, process.create_time())
        else:
            if task.status is not TaskStatus.RUNNING:
                task = await self._sessions.transition_task(task.id, TaskStatus.RUNNING)
        if not callable(begin) and callable(register):
            process = psutil.Process(os.getpid())
            await register(task.id, instance_id, process.pid, process.create_time())
        try:
            started = AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": task.id, "status": task.status.value, "run_instance_id": instance_id})
            await self._sessions.append_event(task.thread_id, started)
            yield started
            token = cancellation if cancellation is not None else CancellationToken()
            self._tokens[task.id] = token
            instruction = prompt or task.contract.objective
            stream = self._controller.ask(
                instruction,
                thread_id=task.thread_id,
                cancellation=token,
                task=task,
                attachments=attachments,
            )
            if cancellation is not None:
                from .task_stream import cancellable_events
                stream = cancellable_events(stream, token)
            async for event in stream:
                if event.kind is EventKind.CANCELLED:
                    event = await self._record_cancelled_result(task.id, event, instance_id)
                yield event
        except CancellationError as error:
            if cancellation is not None:
                await self._record_stream_interrupt(task.id, error.reason)
            yield await self._record_cancelled_result(task.id,
                AgentEvent(EventKind.CANCELLED, {'reason': error.reason}), instance_id)
        except asyncio.CancelledError:
            if cancellation is not None:
                cancellation.cancel("client request cancelled")
                await self._settle_children(task.id)
                await self._record_stream_interrupt(task.id, cancellation.reason)
                await self._record_cancelled_result(task.id,
                    AgentEvent(EventKind.CANCELLED, {'reason': cancellation.reason}), instance_id)
            raise
        except Exception as error:
            current = await self._sessions.load_task(task.id)
            if current.status is TaskStatus.RUNNING:
                status = TaskStatus.INTERRUPTED if _is_recoverable_model_failure(error) else TaskStatus.FAILED
                reason = "model request interrupted; resume to continue" if status is TaskStatus.INTERRUPTED else "task execution failed"
                await self._sessions.transition_task(task.id, status, reason)
            raise
        finally:
            self._tokens.pop(task.id, None)
            await self._settle_children(task.id)
            release = getattr(self._sessions, "release_task_execution", None)
            if (callable(begin) or callable(register)) and callable(release):
                await release(task.id, instance_id)

    async def _settle_children(self, task_id: str) -> None:
        """Host overrides this barrier before owner release or pause checkpoint."""

    async def _record_stream_interrupt(self, task_id, reason):
        current = await self._sessions.load_task(task_id)
        if current.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            await self._sessions.transition_task(task_id, TaskStatus.INTERRUPTED, reason)

    async def pause(self, task_id: str, reason: str = "user requested pause") -> None:
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel(reason)
        await self._settle_children(task_id)
        task = await self._sessions.transition_task(task_id, TaskStatus.PAUSED, reason)
        await self._sessions.create_checkpoint(
            task.thread_id,
            "task-paused",
            {"task_id": task.id, "status": task.status.value, "reason": reason},
        )

    async def _record_cancelled_result(self, task_id, event, instance_id):
        """Record a run outcome without competing with pause/stop lifecycle writes."""
        from code_agent.core.task_result import TaskResult
        task = await self._sessions.load_task(task_id)
        state = await self._sessions.load_task_state(task.thread_id)
        # stop() and the runner's cancellation event can reach persistence in
        # either order. Its own explicit FAILED write is still a cancelled run.
        token = self._tokens.get(task_id)
        own_stop = (task.status is TaskStatus.FAILED
                    and task.stop_reason == "user stopped task"
                    and token is not None and token.reason == "user stopped task")
        if own_stop:
            checklist = getattr(self._sessions, "recovery_checklist", None)
            if callable(checklist):
                owner = (await checklist(task_id)).get("execution_owner")
                own_stop = isinstance(owner, Mapping) and owner.get("instance_id") == instance_id
            else:
                own_stop = False
        if task.status.is_terminal and not own_stop:
            result = await self.result(task_id)
            return AgentEvent(EventKind.TASK_RESULT, {'task_id': task_id, 'result': result.to_dict()})
        result = TaskResult('cancelled', 'changed' if state.files_changed else 'unchanged',
                            'unknown', stop_code='cancelled', stop_reason=event.payload.get('reason'))
        payload = {'task_id': task.id, 'task_updated_at': task.updated_at.isoformat(),
                   'run_instance_id': instance_id,
                   'result_generation': state.code_generation, 'result_subject_hash': state.subject_hash,
                   'result': result.to_dict()}
        await self._sessions.append_event(task.thread_id, AgentEvent(EventKind.TASK_RESULT, payload))
        return AgentEvent(event.kind, {**event.payload, **payload}, event.timestamp)

    async def stop(self, task_id: str) -> None:
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel("user stopped task")
        await self._settle_children(task_id)
        await self._sessions.transition_task(task_id, TaskStatus.FAILED, "user stopped task")

    async def accept_partial(self, task_id: str, reason: str = "user accepted partial delivery") -> TaskRecord:
        """Record explicit user acceptance without claiming verified completion."""
        task = await self._sessions.load_task(task_id)
        if task.status not in {TaskStatus.VERIFYING, TaskStatus.WAITING_DECISION}:
            raise RuntimeError("only verifying or waiting tasks can be accepted partially")
        prior_result = await self.result(task_id)
        accepted = await self._sessions.transition_task(task_id, TaskStatus.ACCEPTED_PARTIAL, reason)
        await self._sessions.create_checkpoint(
            accepted.thread_id,
            "task-accepted-partial",
            {"task_id": accepted.id, "status": accepted.status.value, "reason": reason},
        )
        await self._record_partial_result(accepted, prior_result)
        return accepted

    async def _record_partial_result(self, accepted, prior_result):
        """Bind a user decision to current facts without claiming verification."""
        from code_agent.core.task_result import result_from_task
        state = await self._sessions.load_task_state(accepted.thread_id)
        verification = prior_result.verification_status
        if verification not in {"unverified", "partial"}:
            verification = "unknown"
        result = result_from_task(accepted, state, verification=verification,
                                  remaining=prior_result.remaining)
        await self._sessions.append_event(accepted.thread_id, AgentEvent(
            EventKind.TASK_RESULT, {"task_id": accepted.id,
                "task_updated_at": accepted.updated_at.isoformat(),
                "result_generation": state.code_generation,
                "result_subject_hash": state.subject_hash, "result": result.to_dict()}))

    async def record_accepted_partial_result(self, task_id, expected_updated_at, prior_result):
        """Project an already committed operator CAS; never transitions the task."""
        from datetime import datetime
        task = await self._sessions.load_task(task_id)
        expected = datetime.fromisoformat(expected_updated_at.replace('Z', '+00:00'))
        if task.status is not TaskStatus.ACCEPTED_PARTIAL or task.updated_at != expected:
            raise ValueError('accepted partial result no longer matches task facts')
        await self._record_partial_result(task, prior_result)

    async def interrupt(self, task_id: str, reason: str = "TUI closed") -> None:
        """Persist an interrupt before the terminal stops its helper coroutines."""
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel(reason)
        task = await self._sessions.load_task(task_id)
        if task.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            interrupted = await self._sessions.transition_task(task_id, TaskStatus.INTERRUPTED, reason)
            await self._sessions.create_checkpoint(
                interrupted.thread_id,
                "task-interrupted",
                {"task_id": interrupted.id, "status": interrupted.status.value, "reason": reason},
            )

    async def resume(
        self,
        task_id: str,
        instruction: str = "continue safely",
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        async for event in self.events(task_id, instruction, attachments=attachments):
            yield event

    async def resume_thread(
        self,
        thread_id: str,
        instruction: str = "continue safely",
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        """Resume a thread through its task contract when one exists.

        The plain thread CLI used to call ``AgentController.resume`` directly,
        which reused whatever runtime happened to be selected in the current
        process.  Task-owned threads must instead go through ``events`` so the
        persisted model/profile/topology contract is restored before the next
        turn.  Non-task threads keep the legacy conversation semantics.
        """
        task = await self._sessions.load_task_for_thread(thread_id)
        if task is None:
            checkpoints = getattr(self._sessions, "list_checkpoints", None)
            if callable(checkpoints):
                if callable(getattr(self._sessions, "read_event_page", None)):
                    records = await checkpoints(thread_id, label="acp-session-selection",
                                                limit=1, max_bytes=1048576)
                else:
                    records = await checkpoints(thread_id)
                markers = [row for row in records if row.label == "acp-session-selection"]
                if markers:
                    source = markers[-1].metadata.get("source_root")
                    if (not isinstance(source, str)
                            or os.path.normcase(os.path.realpath(source)) != os.path.normcase(os.path.realpath(self._root))):
                        raise ValueError("conversation belongs to a different workspace")
            stats = getattr(self._sessions, "history_stats", None)
            occupied = ((await stats(thread_id))["message_count"] if callable(stats)
                        else bool(await self._sessions.load_messages(thread_id)))
            if occupied:
                raise RuntimeError("legacy conversation needs an explicit workspace-owned continuation")
            created = await self.start(instruction, thread_id=thread_id)
            async for event in self.events(
                created.id, instruction, attachments=attachments
            ):
                yield event
            return
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.ACCEPTED_PARTIAL,
            TaskStatus.FAILED,
            TaskStatus.SUPERSEDED,
        }:
            raise RuntimeError(
                f"task {task.id} is terminal ({task.status.value}); open it without a prompt or use a new task"
            )
        async for event in self.events(
            task.id, instruction, attachments=attachments
        ):
            yield event

    async def steer(
        self,
        task_id: str,
        instruction: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> None:
        checked = freeze_attachments(tuple(attachments))
        if (
            not isinstance(instruction, str)
            or len(instruction) > 1024
            or (not instruction.strip() and not checked)
        ):
            raise ValueError("instruction or attachments must be bounded input")
        control = instruction if instruction.strip() else "apply attached user input"
        await self._sessions.record_task_steering(
            task_id,
            Message(role="user", content=instruction, attachments=checked),
            control,
        )

    async def queue(
        self,
        task_id: str,
        instruction: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> str:
        checked = freeze_attachments(tuple(attachments))
        if (
            not isinstance(instruction, str)
            or len(instruction) > 1024
            or (not instruction.strip() and not checked)
        ):
            raise ValueError("instruction or attachments must be bounded input")
        control = instruction if instruction.strip() else "apply attached user input"
        return await self._sessions.record_task_followup(
            task_id,
            Message(role="user", content=instruction, attachments=checked),
            control,
        )

    async def reconcile_stale_tasks(self) -> tuple[str, ...]:
        reconcile = getattr(self._sessions, "reconcile_stale_tasks", None)
        if not callable(reconcile):
            return ()
        return await reconcile(_owner_is_alive)

    async def result(self, task_id: str):
        """Project current durable facts; old records never imply verified success."""
        from code_agent.core.task_result import TaskResult, result_from_task
        task = await self._sessions.load_task(task_id)
        state = await self._sessions.load_task_state(task.thread_id)
        result = result_from_task(task, state)
        latest_events = getattr(self._sessions, "load_latest_task_events", None)
        if callable(latest_events):
            events = await latest_events(task.id, state.code_generation, state.subject_hash)
        else:
            events = await self._sessions.load_events(task.thread_id)
        latest_run = next((event.payload.get('run_instance_id') for event in reversed(events)
            if event.kind is EventKind.TASK_STATUS_CHANGED
            and event.payload.get('task_id') == task.id
            and event.payload.get('status') == TaskStatus.RUNNING.value), None)
        for event in reversed(events):
            payload = event.payload
            if (payload.get('task_id') == task.id
                    and payload.get('result_generation') == state.code_generation
                    and payload.get('result_subject_hash') == state.subject_hash
                    and isinstance(payload.get('result'), Mapping)):
                recorded = TaskResult.from_dict(payload['result'])
                if (recorded.execution_status == 'cancelled' and latest_run
                        and payload.get('run_instance_id') == latest_run
                        and task.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING,
                            TaskStatus.FAILED, TaskStatus.PAUSED, TaskStatus.INTERRUPTED}):
                    return recorded
                if (payload.get('task_updated_at') == task.updated_at.isoformat()
                        and recorded.execution_status == result.execution_status):
                    return recorded
        return result


def _reconciliation_owner_alive(owner_pid: int, owner_create_time: float) -> bool:
    """Ambiguous process access is not proof that an execution has stopped."""
    try:
        return abs(psutil.Process(owner_pid).create_time() - owner_create_time) < 0.01
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def _owner_is_alive(owner_pid: int, owner_create_time: float) -> bool:
    try:
        return abs(psutil.Process(owner_pid).create_time() - owner_create_time) < 0.01
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def _is_recoverable_model_failure(error: BaseException) -> bool:
    """Recognize provider timeouts/stream interruptions that can resume history."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        if type(current).__name__ == "ModelStreamError":
            cause = current.__cause__
            if cause is None or isinstance(cause, TimeoutError):
                return True
        if bool(getattr(current, "retryable", False)):
            return True
        current = current.__cause__
    return False


def freeze_task_contract(
    prompt: str,
    authorization: TaskAuthorization,
    profile: tuple[str, ...] | None,
    *,
    interaction_mode: str = "code",
) -> TaskContract:
    """Freeze provider facts and, when available, the full runtime selection."""
    intent = infer_task_intent(prompt, interaction_mode)

    if profile is None:
        return TaskContract(
            prompt, authorization, intent=intent, interaction_mode=interaction_mode
        )
    if len(profile) not in {4, 8, 9} or not all(
        isinstance(value, str) and value.strip() for value in profile
    ):
        raise ValueError("profile supplier must return four, eight or nine text facts")
    runtime = profile[4:8] if len(profile) >= 8 else (None,) * 4
    context_selection = None
    if len(profile) == 9:
        import json
        context_selection = json.loads(profile[8])
    return TaskContract(
        prompt,
        authorization,
        intent=intent,
        profile_id=profile[0],
        model=profile[1],
        protocol=profile[2],
        endpoint_host=profile[3],
        agent_topology=runtime[0],
        reasoning_effort=runtime[1],
        runtime_mode=runtime[2],
        runtime_selection_digest=runtime[3],
        interaction_mode=interaction_mode,
        context_selection=context_selection,
    )


def resolved_task_mode(prompt: str, selected_mode: str) -> str:
    """Resolve the UI-only auto policy before persisting a task contract."""
    if selected_mode != "auto":
        return selected_mode
    if infer_task_intent(prompt, "code").value == "analyze":
        return "ask"
    return "code"


def authorization_for_task_mode(
    workspace_root: str, interaction_mode: str
) -> TaskAuthorization:
    if interaction_mode in {"ask", "plan"}:
        return TaskAuthorization(
            workspace_root,
            allow_workspace_write=False,
            allow_local_execute=False,
        )
    return TaskAuthorization.local_workspace(workspace_root)
