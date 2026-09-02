from __future__ import annotations

from typing import AsyncIterator

from ._engine_run import _RunState, _TurnState
from .errors import EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .models import ContextBundle, Message
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
            state.allowed_tool_names, state.disclosed_tool_names
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
        automatic = await self._run_suggested_verification(
            state.thread_id,
            task,
            state.token,
            state.supervisor,
            state.budget,
        )
        if automatic is not None:
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
            next_task = await self._resolve_task_completion(
                task, state.thread_id
            )
            if next_task.status is TaskStatus.RUNNING:
                return
            async for event in self._persist_completion_events(
                state, next_task
            ):
                yield event
            state.stop_requested = True
            return
        next_task = await self._resolve_task_completion(task, state.thread_id)
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
        for call in turn.calls:
            state.used_call_ids.add(call.id)
            async for event in self._dispatch(
                state.thread_id,
                call,
                state.token,
                is_available=call.name in turn.tool_names,
                task=state.task,
                supervisor=state.supervisor,
            ):
                if event.kind is EventKind.MESSAGE_ADDED:
                    message = Message.from_dict(event.payload["message"])
                    state.messages += (message,)
                disclosed = self._disclosed_tool_from_event(event)
                if disclosed is not None:
                    state.disclosed_tool_names.add(disclosed)
                yield event
                if event.kind in {
                    EventKind.TASK_PAUSED,
                    EventKind.TASK_DECISION_REQUIRED,
                }:
                    state.stop_requested = True
                    return
