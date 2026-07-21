from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

from code_agent.interfaces.capability_view import ModePermissionView
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.workflows.observations import TaskCreatedObservation
from code_agent.core.events import EventKind
from code_agent.core.models import ActionResult
from code_agent.core.cancellation import CancellationToken
from code_agent.verification.evidence import EvidenceOutcome
from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.observations import (
    DeliveryObservation,
    EvidenceInvalidatedObservation,
    VerificationObservation,
    RecoveryObservation,
)
from code_agent.interfaces.mode_control import ModeSummary
from code_agent.orchestration.models import AgentMode, ModeSnapshot
from code_agent.orchestration.plugin_extensions import PluginModeCatalog


class ModeAwareWindowsTerminalApp(WindowsTerminalApp):
    def __init__(self, *args: object, capability: ModePermissionView, plugin_errors: tuple[str, ...] = (), **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._capability = capability
        self._plugin_errors = plugin_errors
        self._announced = False

    def update_capability(self, capability: ModePermissionView) -> None:
        self._capability = capability

    async def run(self, *, thread_id: str | None = None) -> None:
        if not self._announced:
            for line in self._capability.lines():
                self._append(DisplayKind.METADATA, line)
            if self._plugin_errors:
                self._append(
                    DisplayKind.WARNING,
                    f"plugins: {len(self._plugin_errors)} contribution(s) unavailable",
                )
            self._announced = True
        await super().run(thread_id=thread_id)


class GitDiffAdapter:
    def __init__(self, git: object | None) -> None:
        self._git = git

    async def read_diff(self, paths: tuple[str, ...] = ()) -> str:
        if self._git is None:
            return ""
        return await asyncio.to_thread(self._git.diff, paths)


class PluginModeControl:
    def __init__(
        self,
        base: object,
        catalog: PluginModeCatalog,
        identifiers: tuple[str, ...],
        apply: object,
        plugin_digests: set[str],
    ) -> None:
        self._base, self._catalog = base, catalog
        self._identifiers, self._apply = identifiers, apply
        self._current: ModeSummary | None = None
        self._plugin_digests = plugin_digests

    @property
    def current(self) -> ModeSummary:
        return self._current or self._base.current

    def list(self) -> tuple[ModeSummary, ...]:
        plugin = tuple(
            self._summary(self._catalog.resolve(identifier))
            for identifier in self._identifiers
        )
        return self._base.list() + plugin

    async def use(self, name: str, *, idle: bool) -> ModeSummary:
        if "." not in name:
            result = await self._base.use(name, idle=idle)
            self._current = None
            return result
        if not idle:
            raise RuntimeError("mode switching is available only when idle")
        contributed = self._catalog.resolve(name)
        definition = replace(
            contributed.base.definition,
            prompt_policy=contributed.prompt_policy,
            tool_names=contributed.tool_names,
            reasoning_effort=contributed.reasoning_effort,
            description=(
                contributed.base.definition.description
                + f" Plugin mode {contributed.identifier}."
            ),
        )
        digest = hashlib.sha256(
            (
                contributed.base.digest
                + contributed.identifier
                + contributed.digest
                + str(contributed.generation)
            ).encode("utf-8")
        ).hexdigest()
        snapshot = ModeSnapshot(
            definition,
            contributed.base.model,
            contributed.base.oracle_model,
            digest,
        )
        self._plugin_digests.add(digest)
        try:
            await self._apply(snapshot)
        except Exception:
            self._plugin_digests.discard(digest)
            raise
        self._current = self._summary(contributed)
        return self._current

    @staticmethod
    def _summary(mode: object) -> ModeSummary:
        return ModeSummary(
            mode.identifier, mode.base.model, mode.reasoning_effort.value
        )


class IntegratedForegroundTaskController(ForegroundTaskController):
    def __init__(
        self,
        *args: object,
        subagents: object,
        workflows: object,
        plugin_events: object | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._subagents = subagents
        self.workflows = workflows
        self._plugin_events = plugin_events

    async def start(self, prompt: str):
        task = await super().start(prompt)
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
            raw = event.payload.get("result")
            if not isinstance(raw, dict):
                return
            result = ActionResult.from_dict(raw)
            if result.name == "run_verification":
                evidence = await self._sessions.list_verification_evidence(
                    task_id
                )
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
            elif (
                result.name in {"write_file", "replace_text"}
                and not result.is_error
            ):
                snapshot = await self._sessions.load_workflow_for_task(task_id)
                if snapshot is not None:
                    for node in snapshot.nodes:
                        if (
                            node.kind == "verification"
                            and node.status is WorkflowNodeStatus.COMPLETED
                        ):
                            await self.workflows.observe(
                                EvidenceInvalidatedObservation(
                                    task_id, node.id
                                )
                            )
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


def _plugin_event_fields(event: object) -> dict[str, object]:
    allowed = {
        "status",
        "name",
        "turn",
        "task_id",
        "thread_id",
        "request_id",
    }
    return {
        key: value
        for key, value in event.payload.items()
        if key in allowed
        and (
            isinstance(value, (str, int, bool, float))
            or value is None
        )
    }


def _plugin_event_kind(event: object) -> str:
    if event.kind is EventKind.TASK_STATUS_CHANGED:
        status = event.payload.get("status")
        if isinstance(status, str):
            return f"task_{status}"
    if event.kind is EventKind.COMPLETED:
        return "run_completed"
    return event.kind.value
