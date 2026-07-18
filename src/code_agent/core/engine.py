from __future__ import annotations

from typing import AsyncIterator, Mapping, Optional

from ._json import JSONValue, freeze_mapping
from ._engine_run import AgentEngineRunMixin, _validate_run_arguments
from ._engine_turn import AgentEngineTurnMixin
from ._session_io import SessionJournal
from .cancellation import CancellationError, CancellationToken
from .engine_actions import AgentEngineActionMixin
from .engine_completion import AgentEngineCompletionMixin
from .errors import AgentEngineError, EngineLimitError
from .events import AgentEvent, EventKind
from .limits import EngineLimits
from .protocols import ActionDispatcher, ContextBuilder, ModelClient, SessionRepository
from .task import TaskRecord
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
    ) -> None:
        self._model = model
        self._context = context
        self._actions = actions
        self._journal = SessionJournal(sessions)
        self._limits = limits or EngineLimits()
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be non-blank text")
        self._model_name = model_name
        self._verification = verification
        self._context_mode_snapshot = freeze_mapping({} if context_mode_snapshot is None else context_mode_snapshot, "context_mode_snapshot")
        self._context_permission_snapshot = freeze_mapping({} if context_permission_snapshot is None else context_permission_snapshot, "context_permission_snapshot")

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        _validate_run_arguments(user_input, thread_id)
        state, started = await self._start_run(thread_id, cancellation, task)
        yield started

        try:
            added, user_message = await self._prepare_request(state, user_input)
            yield added
            state.messages = state.prior_messages + (user_message,)
            for turn in range(1, state.budget.limits.max_agent_rounds + 1):
                state.token.raise_if_cancelled()
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
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={"code": exc.code, "error_type": type(exc).__name__},
            )
            await self._journal.append_event(state.thread_id, failed)
            yield failed
            raise
