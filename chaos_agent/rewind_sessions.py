from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from code_agent.capabilities import CapabilityStrategy
from code_agent.core._json import JSONValue
from code_agent.core.action_execution import ActionExecutionContext, ActionLineage
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.orchestration.models import AgentDefinition, ModeSnapshot
from code_agent.providers.config import ModelProfile
from code_agent.sessions.rewind_models import CoverageToken, RewindBaseline
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore

from code_agent_win.rewind_capture import RewindCaptureCoordinator
from code_agent_win.rewind_gate import WorkspaceMutationGate
from code_agent_win.subagents import RestrictedDispatcher
from code_agent_win.task_verification import TaskScopedVerificationService


_Result = TypeVar("_Result")


@dataclass(frozen=True)
class _Settled(Generic[_Result]):
    value: _Result | None = None
    error: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None


class CoordinatedSessionRepository:
    """Serialize anchored checkpoints with every workspace mutation."""

    def __init__(
        self,
        base: RewindSessionRepository,
        gate: WorkspaceMutationGate,
        workspace_fingerprint: str,
        *,
        fixed_owner_thread_id: str | None = None,
    ) -> None:
        self._base = base
        self._gate = gate
        self._workspace_fingerprint = CoverageToken(
            workspace_fingerprint, 1
        ).workspace_fingerprint
        self._fixed_owner_thread_id = _owner(
            fixed_owner_thread_id, optional=True
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._base, name)

    def for_owner(
        self, owner_thread_id: str
    ) -> "CoordinatedSessionRepository":
        return CoordinatedSessionRepository(
            self._base,
            self._gate,
            self._workspace_fingerprint,
            fixed_owner_thread_id=_owner(owner_thread_id),
        )

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> str:
        lease = await self._gate.acquire()
        try:
            coverage = await _ordered_call(
                self._base.ensure_rewind_coverage(self._workspace_fingerprint)
            )
            owner = self._fixed_owner_thread_id or thread_id
            anchor = await _ordered_call(
                self._base.get_rewind_checkpoint_anchor(coverage.token, owner)
            )
            return await _ordered_call(
                self._base.create_checkpoint(
                    thread_id, label, metadata, rewind_anchor=anchor
                )
            )
        finally:
            await lease.release()


@dataclass(frozen=True)
class RewindWriteSide:
    base: RewindSessionRepository
    coordinated: CoordinatedSessionRepository
    capture: RewindCaptureCoordinator
    snapshots: WorkspaceSnapshotStore
    gate: WorkspaceMutationGate


def build_engine(
    model: object,
    profile: ModelProfile,
    context: object,
    dispatcher: object,
    sessions: object,
    workspace_root: Path,
    mode: ModeSnapshot,
    *,
    action_lineage: ActionLineage | None = None,
    capability_strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
) -> AgentEngine:
    mode_limits = mode.definition.limits
    limits = EngineLimits(
        min(profile.max_agent_rounds, mode_limits.max_agent_rounds),
        min(profile.max_tool_calls, mode_limits.max_tool_calls),
        min(
            profile.max_tool_calls_per_round,
            mode_limits.max_tool_calls_per_round,
        ),
        min(
            profile.context_window + profile.max_output_tokens,
            mode_limits.max_total_tokens,
        ),
        mode_limits.max_assistant_chars,
    )
    semantic_snapshot = getattr(context, "semantic_snapshot_for_root", None)
    initial_verification = LedgerTaskVerificationService(
        workspace_root, sessions
    )
    return AgentEngine(
        model,
        context,
        dispatcher,
        sessions,
        limits=limits,
        model_name=profile.provider.model,
        verification=TaskScopedVerificationService(
            sessions,
            semantic_snapshot if callable(semantic_snapshot) else None,
            (workspace_root, initial_verification),
        ),
        action_lineage=action_lineage,
        capability_strategy=capability_strategy,
    )


def build_child_engine_factory(
    host: object,
    model_factory: Callable[..., object],
    context_factory: Callable[[ModeSnapshot, object], object],
) -> Callable[
    [AgentDefinition, ActionExecutionContext | None], tuple[object, object]
]:
    def child_engine(
        agent: AgentDefinition, parent: ActionExecutionContext | None = None
    ) -> tuple[object, object]:
        capability_strategy = getattr(
            getattr(host, "runtime_config", None),
            "capability_strategy",
            CapabilityStrategy.HYBRID,
        )
        profile = host.profiles[agent.mode.profile_id]
        client = model_factory(
            profile.provider,
            reasoning_effort=agent.mode.effective_reasoning_effort,
        )
        restricted = RestrictedDispatcher(
            host.dispatcher, agent.effective_tools
        )
        sessions = (
            host.sessions
            if parent is None
            else host.sessions.for_owner(parent.owner_thread_id)
        )
        lineage = (
            None
            if parent is None
            else ActionLineage(
                parent.owner_thread_id, parent.task_id, parent.request_id
            )
        )
        engine = build_engine(
            client, profile, context_factory(agent.mode, sessions),
            restricted, sessions, host.root, agent.mode,
            action_lineage=lineage,
            capability_strategy=capability_strategy,
        )
        return engine, client

    return child_engine


def build_rewind_write_side(
    guard: WorkspacePathGuard,
    editor: WorkspaceEditor,
    product_state_root: Path,
    session_path: Path,
    *,
    has_git: bool,
) -> RewindWriteSide:
    snapshots = WorkspaceSnapshotStore(
        guard, product_state_root / "rewind-snapshots"
    )
    base = RewindSessionRepository(session_path)
    gate = WorkspaceMutationGate(
        product_state_root, snapshots.workspace_fingerprint
    )
    baseline = (
        RewindBaseline.UNKNOWN
        if has_git
        else RewindBaseline.NON_GIT_EXISTING
    )
    capture = RewindCaptureCoordinator(
        base, editor, snapshots, gate, existing_baseline=baseline
    )
    coordinated = CoordinatedSessionRepository(
        base, gate, snapshots.workspace_fingerprint
    )
    return RewindWriteSide(base, coordinated, capture, snapshots, gate)


async def _ordered_call(awaitable: Awaitable[_Result]) -> _Result:
    outcome = await _settle_task(asyncio.create_task(awaitable))
    if outcome.cancellation is not None:
        raise outcome.cancellation
    if outcome.error is not None:
        raise outcome.error
    return outcome.value  # type: ignore[return-value]


async def _settle_task(task: asyncio.Task[_Result]) -> _Settled[_Result]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    try:
        return _Settled(task.result(), cancellation=cancellation)
    except BaseException as error:
        return _Settled(error=error, cancellation=cancellation)


def _owner(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("owner_thread_id must be a string")
    if not value.strip():
        raise ValueError("owner_thread_id must not be blank")
    return value


__all__ = [
    "CoordinatedSessionRepository",
    "RewindWriteSide",
    "build_child_engine_factory",
    "build_engine",
    "build_rewind_write_side",
]
