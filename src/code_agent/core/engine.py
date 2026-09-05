from __future__ import annotations

from typing import AsyncIterator, Mapping, Optional, Sequence

from code_agent.capabilities import CapabilityStrategy

from ._json import JSONValue, freeze_mapping
from ._engine_run import AgentEngineRunMixin, _TurnState, _validate_run_arguments
from ._engine_turn import AgentEngineTurnMixin
from ._session_io import SessionJournal
from .action_execution import ActionLineage
from .cancellation import CancellationError, CancellationToken
from .engine_actions import AgentEngineActionMixin
from .engine_completion import AgentEngineCompletionMixin
from .errors import AgentEngineError, ContextBuildError, EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .limits import EngineLimits, add_usage
from .models import ContextBundle, ModelEventKind, ToolCall, Usage
from .attachments import AttachmentRef
from .protocols import ActionDispatcher, ContextBuilder, ModelClient, SessionRepository
from .task import TaskRecord, TaskStatus
from .task_supervisor import SupervisionKind
from .task_verification import TaskVerificationService


class AgentEngine(
    AgentEngineCompletionMixin,
    AgentEngineActionMixin,
    AgentEngineRunMixin,
    AgentEngineTurnMixin,
):
    def __init__(
        self,
        model: ModelClient,
        context: ContextBuilder,
        actions: ActionDispatcher,
        sessions: SessionRepository,
        *,
        limits: Optional[EngineLimits] = None,
        model_name: str = "configured-model",
        verification: TaskVerificationService | None = None,
        context_mode_snapshot: Mapping[str, JSONValue] | None = None,
        context_permission_snapshot: Mapping[str, JSONValue] | None = None,
        action_lineage: ActionLineage | None = None,
        peer_tool_names: Sequence[str] = (),
        capability_strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
    ) -> None:
        self._model = model
        self._context = context
        self._actions = actions
        self._journal = SessionJournal(sessions)
        self._limits = limits or EngineLimits()
        if action_lineage is not None and not isinstance(action_lineage, ActionLineage):
            raise TypeError("action_lineage must be an ActionLineage or None")
        self._action_lineage = action_lineage
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be non-blank text")
        self._model_name = model_name
        self._verification = verification
        peer_tools = tuple(peer_tool_names)
        if not all(isinstance(name, str) and name.strip() for name in peer_tools):
            raise ValueError("peer_tool_names must contain non-blank text")
        if len(set(peer_tools)) != len(peer_tools):
            raise ValueError("peer_tool_names must be unique")
        self._peer_tool_names = frozenset(peer_tools)
        if not isinstance(capability_strategy, CapabilityStrategy):
            raise TypeError("capability_strategy must be a CapabilityStrategy")
        self._capability_strategy = capability_strategy
        self._context_mode_snapshot = freeze_mapping({} if context_mode_snapshot is None else context_mode_snapshot, "context_mode_snapshot")
        self._context_permission_snapshot = freeze_mapping({} if context_permission_snapshot is None else context_permission_snapshot, "context_permission_snapshot")

    async def compact_context(
        self,
        thread_id: str,
        cancellation: CancellationToken | None = None,
    ) -> object:
        compact = getattr(self._context, "compact_context", None)
        if not callable(compact):
            raise RuntimeError("semantic context compaction is unavailable")
        return await compact(thread_id, cancellation)

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        checked_attachments = _validate_run_arguments(
            user_input, thread_id, tuple(attachments)
        )
        state, started = await self._start_run(thread_id, cancellation, task)
        yield started

        try:
            added, user_message = await self._prepare_request(
                state, user_input, checked_attachments
            )
            yield added

            state.messages = state.prior_messages + (user_message,)
            for turn in range(1, state.budget.limits.max_agent_rounds + 1):
                async for event in self._run_turn(state, turn, user_input):
                    yield event
                if state.stop_requested:
                    return

            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(state.thread_id, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            async for failed in self._handle_run_failure(state, exc):
                yield failed
            if isinstance(exc, EngineLimitError) and state.task is not None:
                return
            raise

    async def run_peer(
        self,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one peer-triggered turn without manufacturing a user message.

        Peer content is supplied by the context builder through a distinct,
        untrusted peer channel.  This entry point only wakes an idle engine; it
        deliberately does not append the peer text (or a synthetic prompt) to
        the conversation message journal.
        """
        state, started = await self._start_run(thread_id, cancellation, None)
        state.allowed_tool_names = self._peer_tool_names
        yield started

        try:
            state.prior_messages = await self._journal.load_messages(
                state.thread_id
            )
            state.messages = state.prior_messages
            for turn in range(1, state.budget.limits.max_agent_rounds + 1):
                async for event in self._run_turn(state, turn, ""):
                    yield event
                if state.stop_requested:
                    return

            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(state.thread_id, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={"code": exc.code, "error_type": type(exc).__name__},
            )
            await self._journal.append_event(state.thread_id, failed)
            yield failed
            raise
