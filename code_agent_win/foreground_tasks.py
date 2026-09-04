from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path

from code_agent.core.attachments import AttachmentRef
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionResult
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.task_controller import (
    ForegroundTaskController,
    freeze_task_contract,
)
from code_agent.verification.evidence import EvidenceOutcome
from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.observations import (
    DeliveryObservation,
    EvidenceInvalidatedObservation,
    RecoveryObservation,
    VerificationObservation,
)
from code_agent.sessions.errors import SessionNotFound
from code_agent_win.foreground_checkpoint_lifecycle import ForegroundCheckpointLifecycle
from code_agent_win.foreground_task_observers import observe_task_created
from code_agent_win.foreground_task_support import (
    active_task,
    plugin_event_fields,
    plugin_event_kind,
    same_path,
)
from code_agent_win.foreground_workspace_setup import (
    abort_prepared_workspace,
    bind_workspace,
    prepare_workspace,
)


class IntegratedForegroundTaskController(ForegroundTaskController):
    def __init__(
        self,
        *args: object,
        subagents: object,
        workflows: object,
        plugin_events: object | None = None,
        workspace_runtime: object | None = None,
        checkpoints: object | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._subagents = subagents
        self.workflows = workflows
        self._plugin_events = plugin_events
        self._workspace_runtime = workspace_runtime
        self._settled_callbacks: list[object] = []
        if checkpoints is None and workspace_runtime is not None:
            checkpoints = workspace_runtime.checkpoint_control()
        self._checkpoint_lifecycle = ForegroundCheckpointLifecycle(
            self, self._sessions, checkpoints, workspace_runtime
        )
        if workspace_runtime is not None:
            workspace_runtime.set_quiescer(self.quiesce)

    async def start(self, prompt: str):
        task = await self._create_managed_task(prompt)
        await observe_task_created(
            self.workflows,
            self._plugin_events,
            self._sessions,
            task,
            prompt,
        )
        return task

    async def _create_managed_task(self, prompt: str):
        if await self._has_active_source_task():
            raise RuntimeError("a foreground task is already active")
        task = None
        workspace = None
        try:
            thread_id = await self._sessions.create_thread()
            workspace = await prepare_workspace(
                self._workspace_runtime, Path(self._root)
            )
            root = Path(workspace.worktree_root if workspace else self._root)
            task = await self._sessions.create_task(
                thread_id, self._contract(prompt, root)
            )
            engine = self._controller._engine
            await self._sessions.get_or_create_task_budget(
                thread_id,
                getattr(engine, "_model_name", task.contract.model or "configured-model"),
                getattr(engine, "_limits", EngineLimits()),
            )
            await bind_workspace(
                self._workspace_runtime, thread_id, task.id, root, workspace
            )
            await self._checkpoint_lifecycle.capture(
                task.id,
                "task-created",
                {"task_id": task.id, "status": task.status.value},
                0,
            )
        except BaseException as error:
            await abort_prepared_workspace(
                self._workspace_runtime, workspace, error
            )
            if task is not None:
                await self._interrupt_failed_creation(task.id)
            raise
        assert task is not None
        return task

    async def _interrupt_failed_creation(
        self,
        task_id: str,
        reason: str = "task creation failed before initial checkpoint",
    ) -> None:
        try:
            task = await self._sessions.load_task(task_id)
            if task.status is TaskStatus.CREATED:
                await self._sessions.transition_task(
                    task_id,
                    TaskStatus.INTERRUPTED,
                    reason,
                )
        except Exception:
            # Preserve the setup failure; startup reconciliation can retry cleanup.
            return

    async def _has_active_source_task(self) -> bool:
        for task in await self._sessions.list_tasks():
            if not active_task(task):
                continue
            source = await self._task_source_root(task)
            if same_path(source, Path(self._root)):
                return True
        return False

    async def _task_source_root(self, task: object) -> Path:
        if self._workspace_runtime is not None:
            try:
                lineage = await self._sessions.load_lineage_for_task(task.id)
                return Path(lineage.source_root)
            except SessionNotFound:
                pass
        return Path(task.contract.authorization.workspace_root)

    def _contract(self, prompt: str, root: Path) -> TaskContract:
        profile = self._profile_supplier() if self._profile_supplier else None
        return freeze_task_contract(
            prompt, TaskAuthorization.local_workspace(str(root)), profile
        )

    async def reconcile_stale_tasks(self) -> tuple[str, ...]:
        reconciled = await super().reconcile_stale_tasks()
        for task_id in reconciled:
            if await self._sessions.load_workflow_for_task(task_id) is not None:
                await self.workflows.observe(
                    RecoveryObservation(task_id, frozenset())
                )
        return reconciled

    async def quiesce(self, task_id: str) -> None:
        await self._checkpoint_lifecycle.quiesce(task_id)

    def subscribe_settled(self, callback: object) -> None:
        if not callable(callback):
            raise TypeError("settled callback must be callable")
        self._settled_callbacks.append(callback)

    async def pause(
        self, task_id: str, reason: str = "user requested pause"
    ) -> None:
        await self._checkpoint_lifecycle.pause(task_id, reason)

    async def interrupt(self, task_id: str, reason: str = "TUI closed") -> None:
        await self._checkpoint_lifecycle.interrupt(task_id, reason)

    async def accept_partial(
        self,
        task_id: str,
        reason: str = "user accepted partial delivery",
    ):
        return await self._checkpoint_lifecycle.accept_partial(task_id, reason)

    async def events(
        self,
        task_id: str,
        prompt: str | None = None,
        *,
        attachments: Sequence[AttachmentRef] = (),
    ):
        serial = self._checkpoint_lifecycle.begin_run(task_id)
        token = None
        plugin_token = CancellationToken()
        try:
            token = self._subagents.activate(task_id)
            async for event in super().events(
                task_id, prompt, attachments=attachments
            ):
                await self._observe_workflow_event(task_id, event)
                if self._plugin_events is not None:
                    await self._plugin_events.observe(
                        plugin_event_kind(event),
                        task_id,
                        plugin_event_fields(event),
                        plugin_token,
                    )
                yield event
        finally:
            plugin_token.cancel("task event stream closed")
            try:
                try:
                    if token is not None:
                        await self._subagents.release(task_id)
                finally:
                    if token is not None:
                        self._subagents.reset(token)
                    self._checkpoint_lifecycle.settle_run(task_id, serial)
            finally:
                try:
                    await self._checkpoint_lifecycle.capture_settled(task_id, serial)
                finally:
                    try:
                        await self._notify_settled()
                    finally:
                        self._checkpoint_lifecycle.finalize_run(task_id, serial)

    async def _notify_settled(self) -> None:
        for callback in tuple(self._settled_callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                continue

    async def _observe_workflow_event(self, task_id: str, event: object) -> None:
        if event.kind is EventKind.ACTION_COMPLETED:
            await self._observe_action(task_id, event)
        elif (
            event.kind is EventKind.TASK_STATUS_CHANGED
            and event.payload.get("status") == "completed"
        ):
            await self.workflows.observe(
                DeliveryObservation(
                    task_id,
                    f"delivery:{task_id}",
                    WorkflowNodeStatus.COMPLETED,
                )
            )

    async def _observe_action(self, task_id: str, event: object) -> None:
        raw = event.payload.get("result")
        if not isinstance(raw, Mapping):
            return
        result = ActionResult.from_dict(raw)
        if result.name == "run_verification":
            await self._observe_verification(task_id)
        elif (
            result.name in {"write_file", "replace_text"} and not result.is_error
        ) or (
            result.name == "apply_workspace_edit_plan_v1"
            and result.output.get("workspace_may_have_changed") is True
        ):
            await self._invalidate_completed_evidence(task_id)
        elif result.name in {"run_command", "run_process_v1"} and result.metadata.get("execution_attempted") is True:
            await self._invalidate_completed_evidence(task_id)

    async def _observe_verification(self, task_id: str) -> None:
        evidence = await self._sessions.list_verification_evidence(task_id)
        if not evidence:
            return
        latest = evidence[-1]
        await self.workflows.observe(
            VerificationObservation(
                task_id,
                f"verification:{latest.identifier}",
                latest.outcome is EvidenceOutcome.PASS,
                (latest.identifier,),
            )
        )

    async def _invalidate_completed_evidence(self, task_id: str) -> None:
        snapshot = await self._sessions.load_workflow_for_task(task_id)
        if snapshot is None:
            return
        for node in snapshot.nodes:
            if (
                node.kind == "verification"
                and node.status is WorkflowNodeStatus.COMPLETED
            ):
                await self.workflows.observe(
                    EvidenceInvalidatedObservation(task_id, node.id)
                )
