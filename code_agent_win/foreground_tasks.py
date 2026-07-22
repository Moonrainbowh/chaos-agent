from __future__ import annotations

import os
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.models import ActionResult
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.verification.evidence import EvidenceOutcome
from code_agent.workspace.errors import WorkspaceError
from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.observations import (
    DeliveryObservation,
    EvidenceInvalidatedObservation,
    RecoveryObservation,
    TaskCreatedObservation,
    VerificationObservation,
    )

from code_agent_win.tool_support import discover_git_workspace


class IntegratedForegroundTaskController(ForegroundTaskController):
    def __init__(
        self,
        *args: object,
        subagents: object,
        workflows: object,
        plugin_events: object | None = None,
        workspace_runtime: object | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._subagents = subagents
        self.workflows = workflows
        self._plugin_events = plugin_events
        self._workspace_runtime = workspace_runtime

    async def start(self, prompt: str):
        task = await self._create_managed_task(prompt)
        await self.workflows.observe(
            TaskCreatedObservation(task.id, task.thread_id, prompt)
        )
        if self._plugin_events is not None:
            await self._plugin_events.observe(
                "task_created",
                task.id,
                {"task_id": task.id, "thread_id": task.thread_id},
                CancellationToken(),
            )
        return task

    async def _create_managed_task(self, prompt: str):
        if any(
            _same_active_source(task, self._root)
            for task in await self._sessions.list_tasks()
        ):
            raise RuntimeError("a foreground task is already active")
        thread_id = await self._sessions.create_thread()
        workspace = await self._prepare_workspace()
        root = Path(workspace.worktree_root if workspace else self._root)
        task = await self._sessions.create_task(
            thread_id, self._contract(prompt, root)
        )
        await self._bind_workspace(thread_id, task.id, root, workspace)
        await self._sessions.create_checkpoint(
            thread_id,
            "task-created",
            {"task_id": task.id, "status": task.status.value},
        )
        return task

    def _contract(self, prompt: str, root: Path) -> TaskContract:
        profile = self._profile_supplier() if self._profile_supplier else None
        return TaskContract(
            prompt,
            TaskAuthorization.local_workspace(str(root)),
            profile_id=profile[0] if profile else None,
            model=profile[1] if profile else None,
            protocol=profile[2] if profile else None,
            endpoint_host=profile[3] if profile else None,
        )

    async def _prepare_workspace(self):
        if self._workspace_runtime is None:
            return None
        source = Path(self._root)
        if discover_git_workspace(source) is None:
            return None
        try:
            return await self._workspace_runtime.prepare_task(source, "pending")
        except WorkspaceError as error:
            raise RuntimeError("managed task worktree could not be prepared") from error

    async def _bind_workspace(
        self, thread_id: str, task_id: str, root: Path, workspace: object | None
    ) -> None:
        if self._workspace_runtime is None:
            return
        if workspace is not None:
            await self._workspace_runtime.create_lineage(workspace, task_id)
            root = workspace.worktree_root
        self._workspace_runtime.bind_task(task_id, root)
        self._workspace_runtime.bind_thread(thread_id, root)

    async def reconcile_stale_tasks(self) -> tuple[str, ...]:
        reconciled = await super().reconcile_stale_tasks()
        for task_id in reconciled:
            if await self._sessions.load_workflow_for_task(task_id) is not None:
                await self.workflows.observe(
                    RecoveryObservation(task_id, frozenset())
                )
        return reconciled

    async def events(self, task_id: str, prompt: str | None = None):
        token = self._subagents.activate(task_id)
        plugin_token = CancellationToken()
        try:
            async for event in super().events(task_id, prompt):
                await self._observe_workflow_event(task_id, event)
                if self._plugin_events is not None:
                    await self._plugin_events.observe(
                        _plugin_event_kind(event),
                        task_id,
                        _plugin_event_fields(event),
                        plugin_token,
                    )
                yield event
        finally:
            plugin_token.cancel("task event stream closed")
            await self._subagents.release(task_id)
            self._subagents.reset(token)

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
        if not isinstance(raw, dict):
            return
        result = ActionResult.from_dict(raw)
        if result.name == "run_verification":
            await self._observe_verification(task_id)
        elif result.name in {"write_file", "replace_text"} and not result.is_error:
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


def _plugin_event_fields(event: object) -> dict[str, object]:
    allowed = {"status", "name", "turn", "task_id", "thread_id", "request_id"}
    return {
        key: value
        for key, value in event.payload.items()
        if key in allowed
        and (isinstance(value, (str, int, bool, float)) or value is None)
    }


def _plugin_event_kind(event: object) -> str:
    if event.kind is EventKind.TASK_STATUS_CHANGED:
        status = event.payload.get("status")
        if isinstance(status, str):
            return f"task_{status}"
    if event.kind is EventKind.COMPLETED:
        return "run_completed"
    return event.kind.value


def _same_active_source(task: object, source_root: str) -> bool:
    if task.status not in {TaskStatus.CREATED, TaskStatus.RUNNING}:
        return False
    root = task.contract.authorization.workspace_root
    source = getattr(task, "source_root", root)
    return os.path.normcase(source) == os.path.normcase(source_root)
