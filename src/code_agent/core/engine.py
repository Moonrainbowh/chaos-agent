from __future__ import annotations

from typing import AsyncIterator, Mapping, Optional

from ._json import JSONValue, freeze_mapping
from ._engine_run import AgentEngineRunMixin, _validate_run_arguments
from ._engine_turn import AgentEngineTurnMixin
from ._session_io import SessionJournal
from .action_execution import ActionLineage
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
        action_lineage: ActionLineage | None = None,
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

            messages = prior_messages + (user_message,)
            used_call_ids: set[str] = set()
            total_usage = Usage()

            for turn in range(1, task_budget.limits.max_agent_rounds + 1):
                token.raise_if_cancelled()
                if supervisor is not None:
                    decision = supervisor.before_model_turn()
                    if decision.kind is SupervisionKind.PAUSE:
                        await self._pause_task(active_thread, task, supervisor, decision.reason or "task paused")
                        paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "task paused"})
                        await self._journal.append_event(active_thread, paused)
                        yield paused
                        return
                    await self._journal.record_task_active_seconds(
                        task.id, supervisor.checkpoint_active_seconds()
                    )
                    await self._journal.consume_task_controls(task.id)
                reserved = await self._journal.reserve_task_budget(
                    active_thread, model_turns=1
                )
                if reserved is None:
                    raise EngineLimitError("model turn budget exceeded")
                task_budget = reserved
                tools, tool_names = self._advertised_tools()
                turn_started = AgentEvent(
                    kind=EventKind.TURN_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, turn_started)
                yield turn_started

                source_messages = (
                    await self._journal.load_messages(active_thread)
                    if task is not None
                    else prior_messages if turn == 1 else messages
                )
                source_input = user_input if turn == 1 else ""
                try:
                    task_state = await self._journal.load_task_state(active_thread)
                    bundle = await self._context.build(
                        active_thread,
                        source_messages,
                        source_input,
                        tools,
                        task_state,
                        token,
                    )
                    if not isinstance(bundle, ContextBundle):
                        raise TypeError("context builder returned an invalid bundle")
                except CancellationError:
                    raise
                except Exception:
                    raise ContextBuildError("context build failed") from None

                built = AgentEvent(
                    kind=EventKind.CONTEXT_BUILT,
                    payload={"turn": turn, **bundle.measurements},
                )
                await self._journal.append_event(active_thread, built)
                yield built

                model_started = AgentEvent(
                    kind=EventKind.MODEL_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, model_started)
                yield model_started

                text_parts: list[str] = []
                calls: list[ToolCall] = []
                completed = False
                try:
                    stream = self._model.stream(
                        bundle.system_prompt,
                        bundle.messages,
                        tools,
                    )
                    async for model_event in stream:
                        token.raise_if_cancelled()
                        if completed:
                            raise ModelStreamError(
                                "model emitted an event after completion"
                            )
                        self._accumulate_model_event(
                            model_event,
                            text_parts,
                            calls,
                        )
                        if model_event.kind is ModelEventKind.COMPLETED:
                            completed = True
                        streamed = AgentEvent(
                            kind=EventKind.MODEL_EVENT,
                            payload={"event": model_event.to_dict()},
                        )
                        await self._journal.append_event(active_thread, streamed)
                        yield streamed
                        if model_event.usage is not None:
                            total_usage = add_usage(total_usage, model_event.usage)
                            if task is not None:
                                await self._journal.consume_task_usage(task.id, model_event.usage)
                                for threshold in await self._journal.mark_task_budget_warnings(task.id):
                                    warning = AgentEvent(
                                        EventKind.TASK_BUDGET_WARNING,
                                        {"task_id": task.id, "threshold": threshold, "reason": f"token budget reached {threshold}%"},
                                    )
                                    await self._journal.append_event(active_thread, warning)
                                    yield warning
                            if total_usage.total_tokens > self._limits.max_total_tokens:
                                raise EngineLimitError("token budget exceeded")
                except (AgentEngineError, CancellationError):
                    raise
                except Exception:
                    raise ModelStreamError("model stream failed") from None

                if not completed:
                    raise ModelStreamError("model stream ended before completion")

                assistant, assistant_added = await self._persist_assistant_message(
                    active_thread, text_parts, calls
                )
                messages += (assistant,)
                yield assistant_added

                if not calls:
                    if task is not None:
                        if supervisor is not None:
                            await self._journal.record_task_active_seconds(
                                task.id, supervisor.checkpoint_active_seconds()
                            )
                        automatic = await self._run_suggested_verification(
                            active_thread, task, token, supervisor, task_budget, tool_names
                        )
                        if automatic is not None:
                            task_budget, automatic_events = automatic
                            for action_event in automatic_events:
                                yield action_event
                            if self._should_stop_after_action(automatic_events):
                                return
                            next_task = await self._resolve_task_completion(task, active_thread)
                            if next_task.status is not TaskStatus.RUNNING:
                                for completion_event in self._task_completion_events(
                                    active_thread, next_task, task_budget, total_usage
                                ):
                                    await self._journal.append_event(active_thread, completion_event)
                                    yield completion_event
                                return
                            continue
                        next_task = await self._resolve_task_completion(task, active_thread)
                        for completion_event in self._task_completion_events(
                            active_thread, next_task, task_budget, total_usage
                        ):
                            await self._journal.append_event(active_thread, completion_event)
                            yield completion_event
                        return
                    finished = self._completed_event(active_thread, task_budget, total_usage)
                    await self._journal.append_event(active_thread, finished)
                    yield finished
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
