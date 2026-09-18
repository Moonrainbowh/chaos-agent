from __future__ import annotations

import json
from typing import AsyncIterator

from ._engine_run import _RunState, _TurnState
from ._engine_convergence import queue_runtime_notice
from .engine_turn_feedback import circuit_breaker_result, is_in_flight_failure
from .events import AgentEvent, EventKind
from .models import ActionResult, Message, ToolCall


class AgentEngineDispatchMixin:
    """Execute a model turn's tool calls and persist paired tool feedback."""

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
                state.budget,
            )
            for event in events:
                yield event

    async def _dispatch_turn_call(
        self, state, turn, call, validation_blocked
    ) -> AsyncIterator[AgentEvent]:
        # Register every accepted call before policy handling so a blocked call
        # cannot have its id reused by a later model turn.
        state.used_call_ids.add(call.id)
        failure = circuit_breaker_result(state.action_history, call)
        if failure is not None:
            requested = AgentEvent(EventKind.ACTION_REQUESTED, {"request": {
                "id": call.id, "name": call.name, "arguments": dict(call.arguments)
            }})
            await self._journal.append_event(state.thread_id, requested)
            yield requested
            for event in await self._persist_tool_failure(
                state.thread_id, call, failure
            ):
                self._track_turn_event(state, event, validation_blocked)
                yield event
            return
        blocked = validation_blocked[0]
        completed_result = None
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
            if event.kind is EventKind.ACTION_COMPLETED:
                completed_result = event.payload.get("result")
            yield event
            if event.kind is EventKind.MESSAGE_ADDED and completed_result is not None:
                try:
                    observation = state.exploration_repeat.observe(
                        call, ActionResult.from_dict(completed_result)
                    )
                except (KeyError, TypeError, ValueError):
                    observation = None
                if observation is not None:
                    notice = AgentEvent(
                        EventKind.TASK_BUDGET_WARNING,
                        {
                            "category": "exact_repeat",
                            "reason": observation.reason,
                            "count": observation.count,
                        },
                    )
                    await self._journal.append_event(state.thread_id, notice)
                    queue_runtime_notice(
                        state, "Runtime control: " + observation.reason + "."
                    )
                    yield notice
                    if observation.kind == "pause":
                        state.stop_requested = True
                        if state.task is not None:
                            await self._pause_task(
                                state.thread_id,
                                state.task,
                                state.supervisor,
                                observation.reason,
                            )
                            paused = AgentEvent(
                                EventKind.TASK_PAUSED,
                                {
                                    "task_id": state.task.id,
                                    "status": "paused",
                                    "reason": observation.reason,
                                },
                            )
                        else:
                            paused = AgentEvent(
                                EventKind.ERROR,
                                {
                                    "code": "exploration_stalled",
                                    "error_type": "ExplorationStalled",
                                    "reason": observation.reason,
                                },
                            )
                        await self._journal.append_event(state.thread_id, paused)
                        yield paused

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
