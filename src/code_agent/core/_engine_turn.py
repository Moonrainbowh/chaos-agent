from __future__ import annotations

import json
from typing import AsyncIterator

from ._engine_run import _RunState, _TurnState
from .engine_turn_feedback import (
    circuit_breaker_result,
    is_in_flight_failure,
    verification_failed,
)
from .errors import EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .models import ContextBundle, Message, ToolCall
from .task import TaskRecord, TaskStatus
from .task_supervisor import SupervisionKind


class AgentEngineTurnMixin:
    """Advance one model turn while preserving durable event ordering."""

    async def _run_turn(
        self, state: _RunState, number: int, user_input: str
    ) -> AsyncIterator[AgentEvent]:
        async for event in self._before_model_turn(state, number):
            yield event
        if state.stop_requested:
            return
        reserved = await self._journal.reserve_task_budget(
            state.thread_id, model_turns=1
        )
        if reserved is None:
            raise EngineLimitError("model turn budget exceeded")
        state.budget = reserved
        tools, tool_names = self._advertised_tools(
            state.allowed_tool_names, state.disclosed_tool_digests
        )
        turn = _TurnState(number, tools, tool_names)
        bundles: list[ContextBundle] = []
        async for event in self._start_turn(state, turn, user_input, bundles):
            yield event
        async for event in self._stream_model_events(state, turn, bundles[0]):
            yield event
        assistant, added = await self._persist_assistant_message(
            state.thread_id, turn.text_parts, turn.calls
        )
        state.messages += (assistant,)
        yield added
        if not turn.calls:
            async for event in self._finish_without_calls(state, turn):
                yield event
            return
        await self._reserve_tool_calls(state, turn)
        async for event in self._dispatch_tool_calls(state, turn):
            yield event

    async def _before_model_turn(
        self, state: _RunState, number: int
    ) -> AsyncIterator[AgentEvent]:
        if state.supervisor is None:
            return
        task = state.task
        assert task is not None
        decision = state.supervisor.before_model_turn()
        if decision.kind is SupervisionKind.PAUSE:
            reason = decision.reason or "task paused"
            await self._pause_task(
                state.thread_id, task, state.supervisor, reason
            )
            paused = AgentEvent(
                EventKind.TASK_PAUSED,
                {"task_id": task.id, "status": "paused", "reason": reason},
            )
            await self._journal.append_event(state.thread_id, paused)
            yield paused
            state.stop_requested = True
            return
        await self._journal.record_task_active_seconds(
            task.id, state.supervisor.checkpoint_active_seconds()
        )
        await self._journal.consume_task_controls(task.id)
        if number > 1:
            followups = await self._promote_task_followups(state, task)
            if followups is not None:
                yield followups

    async def _start_turn(
        self,
        state: _RunState,
        turn: _TurnState,
        user_input: str,
        bundles: list[ContextBundle],
    ) -> AsyncIterator[AgentEvent]:
        started = AgentEvent(
            EventKind.TURN_STARTED, {"turn": turn.number}
        )
        await self._journal.append_event(state.thread_id, started)
        yield started
        bundle = await self._build_turn_context(state, turn, user_input)
        bundles.append(bundle)
        built = AgentEvent(
            EventKind.CONTEXT_BUILT,
            {"turn": turn.number, **bundle.measurements},
        )
        await self._journal.append_event(state.thread_id, built)
        yield built
        model_started = AgentEvent(
            EventKind.MODEL_STARTED, {"turn": turn.number}
        )
        await self._journal.append_event(state.thread_id, model_started)
        yield model_started

    async def _finish_without_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        if state.task is not None:
            async for event in self._finish_task_without_calls(state, turn):
                yield event
            return
        finished = self._completed_event(
            state.thread_id, state.budget, state.total_usage
        )
        await self._journal.append_event(state.thread_id, finished)
        yield finished
        state.stop_requested = True

    async def _finish_task_without_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        task = state.task
        assert task is not None
        if state.supervisor is not None:
            await self._journal.record_task_active_seconds(
                task.id, state.supervisor.checkpoint_active_seconds()
            )
        followups = await self._promote_task_followups(state, task)
        if followups is not None:
            yield followups
            return
        ran_automatic = False
        for _ in range(3):
            automatic = await self._run_suggested_verification(
                state.thread_id,
                task,
                state.token,
                state.supervisor,
                state.budget,
            )
            if automatic is None:
                break
            ran_automatic = True
            state.budget, automatic_events = automatic
            for event in automatic_events:
                yield event
            if self._should_stop_after_action(automatic_events):
                state.stop_requested = True
                return
            followups = await self._promote_task_followups(state, task)
            if followups is not None:
                yield followups
                return
            if verification_failed(automatic_events):
                break
        next_task = await self._resolve_task_completion(task, state.thread_id)
        if ran_automatic and next_task.status is TaskStatus.RUNNING:
            return
        async for event in self._persist_completion_events(state, next_task):
            yield event
        state.stop_requested = True

    async def _promote_task_followups(
        self, state: _RunState, task: TaskRecord
    ) -> AgentEvent | None:
        promoted = await self._journal.promote_task_followups(task.id)
        if not promoted:
            return None
        event = AgentEvent(
            EventKind.TASK_FOLLOWUPS_PROMOTED,
            {
                "task_id": task.id,
                "ids": [identifier for identifier, _ in promoted],
                "count": len(promoted),
            },
        )
        await self._journal.append_event(state.thread_id, event)
        return event

    async def _persist_completion_events(
        self, state: _RunState, task: TaskRecord
    ) -> AsyncIterator[AgentEvent]:
        events = self._task_completion_events(
            state.thread_id, task, state.budget, state.total_usage
        )
        for event in events:
            await self._journal.append_event(state.thread_id, event)
            yield event

    async def _reserve_tool_calls(
        self, state: _RunState, turn: _TurnState
    ) -> None:
        if state.budget.model_turns >= state.budget.limits.max_agent_rounds:
            raise EngineLimitError("model turn budget exceeded")
        if len(turn.calls) > state.budget.limits.max_tool_calls_per_round:
            raise EngineLimitError("tool call per-round budget exceeded")
        reserved = await self._journal.reserve_task_budget(
            state.thread_id, tool_calls=len(turn.calls)
        )
        if reserved is None:
            raise EngineLimitError("tool call budget exceeded")
        state.budget = reserved
        if len({call.id for call in turn.calls}) != len(turn.calls) or any(
            call.id in state.used_call_ids for call in turn.calls
        ):
            raise ModelStreamError("model reused a tool call id")

    async def _dispatch_tool_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        verification = getattr(self, "_verification", None)
        in_turn_tx = False
        milestone_call = None
        validation_blocked = [False]
        if (
            state.task is not None
            and verification is not None
            and hasattr(verification, "begin_logical_change")
            and len(turn.calls) > 0
        ):
            verification.begin_logical_change(state.task.id)
            in_turn_tx = True

        try:
            for call in turn.calls:
                async for event in self._dispatch_turn_call(
                    state, turn, call, validation_blocked
                ):
                    yield event
                if state.stop_requested:
                    return
        finally:
            if in_turn_tx and state.task is not None and verification is not None:
                current_state = await self._journal.load_task_state(state.thread_id)
                settled_state, milestone_call = await verification.commit_logical_change(
                    state.task, current_state
                )
                await self._journal.save_task_state(state.thread_id, settled_state)
        if isinstance(milestone_call, ToolCall) and not state.stop_requested:
            state.budget, events = await self._run_verification_call(
                state.thread_id,
                state.task,
                state.token,
                state.supervisor,
                milestone_call,
            )
            for event in events:
                yield event

    async def _dispatch_turn_call(
        self, state, turn, call, validation_blocked
    ) -> AsyncIterator[AgentEvent]:
        failure = circuit_breaker_result(state.action_history, call)
        if failure is not None:
            for event in await self._persist_tool_failure(
                state.thread_id, call, failure
            ):
                yield event
            return
        state.used_call_ids.add(call.id)
        blocked = validation_blocked[0]
        async for event in self._dispatch(
            state.thread_id,
            call,
            state.token,
            is_available=call.name in turn.tool_names and not blocked,
            unavailable_reason=(
                "blocked after an in-flight validation failure"
                if blocked else "tool is not available"
            ),
            task=state.task,
            supervisor=state.supervisor,
        ):
            self._track_turn_event(state, event, validation_blocked)
            yield event

    async def _persist_tool_failure(self, thread_id, call, result):
        completed = AgentEvent(
            EventKind.ACTION_COMPLETED, {"result": result.to_dict()}
        )
        await self._journal.append_event(thread_id, completed)
        message = Message(
            role="tool", name=call.name, tool_call_id=call.id,
            content=json.dumps(result.to_dict(), ensure_ascii=False),
        )
        await self._journal.append_message(thread_id, message)
        added = self._journal.message_added(message)
        await self._journal.append_event(thread_id, added)
        return completed, added

    def _track_turn_event(self, state, event, validation_blocked) -> None:
        if event.kind is EventKind.MESSAGE_ADDED:
            state.messages += (Message.from_dict(event.payload["message"]),)
        disclosed = self._disclosed_tool_from_event(event)
        if disclosed is not None:
            name, digest = disclosed
            state.disclosed_tool_digests[name] = digest
        if is_in_flight_failure(event):
            validation_blocked[0] = True
        if event.kind in {
            EventKind.TASK_PAUSED,
            EventKind.TASK_DECISION_REQUIRED,
        }:
            state.stop_requested = True
