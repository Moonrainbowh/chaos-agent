from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from ._session_io import SessionJournal
from .cancellation import CancellationError, CancellationToken
from .errors import AgentEngineError, ContextBuildError, EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .limits import EngineLimits, add_usage
from .models import ContextBundle, Message, ModelEvent, ModelEventKind, ToolCall, Usage
from .protocols import ActionDispatcher, ContextBuilder, ModelClient, SessionRepository
from .task import TaskRecord, TaskStatus
from .task_supervisor import SupervisionKind, TaskSupervisor
from .engine_actions import AgentEngineActionMixin
from .task_verification import TaskVerificationService
from .engine_completion import AgentEngineCompletionMixin

class AgentEngine(AgentEngineCompletionMixin, AgentEngineActionMixin):
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

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        if not isinstance(user_input, str):
            raise TypeError("user_input must be a string")
        if not user_input.strip():
            raise ValueError("user_input must not be blank")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id.strip()):
            raise ValueError("thread_id must be a non-blank string or None")

        token = cancellation or CancellationToken()
        active_thread = thread_id or await self._journal.create_thread()
        if task is not None and task.thread_id != active_thread:
            raise ValueError("task must belong to the active thread")
        task_budget = await self._journal.get_or_create_task_budget(
            active_thread, self._model_name, self._limits
        )
        supervisor = TaskSupervisor(task.contract, task_budget) if task else None
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        yield started

        try:
            token.raise_if_cancelled()
            prior_messages = await self._journal.load_messages(active_thread)
            if task is not None and self._verification is not None:
                prepared = await self._verification.prepare(
                    task, await self._journal.load_task_state(active_thread)
                )
                await self._journal.save_task_state(active_thread, prepared)
            user_message = Message(role="user", content=user_input)
            await self._journal.append_message(active_thread, user_message)
            added = self._journal.message_added(user_message)
            await self._journal.append_event(active_thread, added)
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

                if task_budget.model_turns >= task_budget.limits.max_agent_rounds:
                    raise EngineLimitError("model turn budget exceeded")
                if len(calls) > task_budget.limits.max_tool_calls_per_round:
                    raise EngineLimitError("tool call per-round budget exceeded")
                reserved = await self._journal.reserve_task_budget(
                    active_thread, tool_calls=len(calls)
                )
                if reserved is None:
                    raise EngineLimitError("tool call budget exceeded")
                task_budget = reserved
                if len({call.id for call in calls}) != len(calls) or any(
                    call.id in used_call_ids for call in calls
                ):
                    raise ModelStreamError("model reused a tool call id")

                for call in calls:
                    used_call_ids.add(call.id)
                    async for action_event in self._dispatch(
                        active_thread,
                        call,
                        token,
                        is_available=call.name in tool_names,
                        task=task,
                        supervisor=supervisor,
                    ):
                        if action_event.kind is EventKind.MESSAGE_ADDED:
                            result_message = Message.from_dict(
                                action_event.payload["message"]  # type: ignore[arg-type]
                            )
                            messages += (result_message,)
                        yield action_event
                        if action_event.kind in {
                            EventKind.TASK_PAUSED,
                            EventKind.TASK_DECISION_REQUIRED,
                        }:
                            return

            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(active_thread, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={"code": exc.code, "error_type": type(exc).__name__},
            )
            await self._journal.append_event(active_thread, failed)
            yield failed
            raise
