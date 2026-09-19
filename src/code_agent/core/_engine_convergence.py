from __future__ import annotations

from typing import AsyncIterator

from ._engine_run import _RunState, _TurnState
from ._tool_feedback import tool_failure
from .events import AgentEvent, EventKind
from .models import Message


def queue_runtime_notice(state: _RunState, content: str) -> None:
    """Queue one bounded internal control message for the next model turn."""
    notices = getattr(state, "pending_runtime_notices", None)
    if notices is None:
        notices = []
        setattr(state, "pending_runtime_notices", notices)
    notices.append(content[:1000])


class AgentEngineConvergenceMixin:
    """Persist bounded runtime controls and guide tool-only exploration."""

    async def _flush_runtime_notices(
        self, state: _RunState
    ) -> AsyncIterator[AgentEvent]:
        """Persist controls only after the current assistant/tool block closes."""
        while state.pending_runtime_notices:
            content = state.pending_runtime_notices.pop(0)[:1000]
            message = Message(role="developer", content=content)
            await self._journal.append_message(state.thread_id, message)
            added = self._journal.message_added(message)
            if state.task is None:
                state.messages += (message,)
            await self._journal.append_event(state.thread_id, added)
            yield added

    async def _observe_tool_only_convergence(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        if state.stop_requested:
            return
        observation = state.tool_only_guard.observe(
            has_text=bool("".join(turn.text_parts).strip()),
            calls=turn.calls,
        )
        if observation is None:
            return
        warning = AgentEvent(
            EventKind.TASK_BUDGET_WARNING,
            {
                "category": "exploration",
                "phase": observation.kind,
                "reason": observation.reason,
                "count": observation.count,
            },
        )
        await self._journal.append_event(state.thread_id, warning)
        queue_runtime_notice(state, "Runtime control: " + observation.reason + ".")
        yield warning
        async for event in self._flush_runtime_notices(state):
            yield event
        if observation.kind == "finalize":
            state.summary_required = True
            queue_runtime_notice(
                state,
                "Runtime control: the next model turn must provide a final "
                "user-facing summary from the collected evidence; no tools "
                "will be available.",
            )

    async def _report_empty_summary(
        self, state: _RunState
    ) -> AsyncIterator[AgentEvent]:
        """Make a missing final answer visible without inventing a result."""
        event = AgentEvent(
            EventKind.ERROR,
            {
                "code": "empty_summary",
                "reason": "model completed the summary turn without answer text",
            },
        )
        await self._journal.append_event(state.thread_id, event)
        yield event

    async def _reject_summary_tool_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        """Close a non-compliant summary turn without executing tools."""
        reason = "summary-only turn requested tools; no tool was executed"
        for call in turn.calls:
            state.used_call_ids.add(call.id)
            requested = AgentEvent(
                EventKind.ACTION_REQUESTED,
                {
                    "request": {
                        "id": call.id,
                        "name": call.name,
                        "arguments": dict(call.arguments),
                    }
                },
            )
            await self._journal.append_event(state.thread_id, requested)
            yield requested
            failure = tool_failure(
                call, reason, error_code="summary_tool_call_rejected"
            )
            for event in await self._persist_tool_failure(
                state.thread_id, call, failure
            ):
                yield event
        if turn.forced_summary and state.summary_retry_count == 0:
            state.summary_retry_count += 1
            state.summary_required = True
            queue_runtime_notice(
                state,
                "Runtime control: tools remain unavailable. Reply now with the "
                "final user-facing summary only; do not request another tool.",
            )
            return
        state.stop_requested = True
        if state.task is not None:
            await self._pause_task(
                state.thread_id, state.task, state.supervisor, reason
            )
            terminal = AgentEvent(
                EventKind.TASK_PAUSED,
                {
                    "task_id": state.task.id,
                    "status": "paused",
                    "reason": reason,
                },
            )
        else:
            terminal = AgentEvent(
                EventKind.ERROR,
                {
                    "code": "exploration_stalled",
                    "error_type": "ExplorationStalled",
                    "reason": reason,
                },
            )
        await self._journal.append_event(state.thread_id, terminal)
        yield terminal
