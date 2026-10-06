from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, Sequence

from code_agent.core.events import AgentEvent
from code_agent.core.models import Message
from code_agent.core.task import TaskRecord
from code_agent.core.task_result import TaskResult
from code_agent.sessions.models import CheckpointRecord, GoalRecord


class ThreadHistoryReader(Protocol):
    """Loads persisted records needed to reconstruct one thread."""

    async def load_messages(self, thread_id: str) -> Sequence[Message]: ...

    async def load_events(self, thread_id: str) -> Sequence[AgentEvent]: ...

    async def list_goals(self, thread_id: str) -> Sequence[GoalRecord]: ...

    async def list_checkpoints(
        self, thread_id: str
    ) -> Sequence[CheckpointRecord]: ...


@dataclass(frozen=True)
class RestoredThread:
    thread_id: str
    messages: tuple[Message, ...]
    events: tuple[AgentEvent, ...]
    goals: tuple[GoalRecord, ...]
    checkpoints: tuple[CheckpointRecord, ...]
    message_count: int | None = None
    event_count: int | None = None
    truncated: bool = False
    task: TaskRecord | None = None
    task_result: TaskResult | None = None


async def load_thread_history(
    reader: ThreadHistoryReader, thread_id: str
) -> RestoredThread:
    """Restore a bounded current view; legacy readers keep their old contract."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be non-blank text")

    if (callable(getattr(reader, "read_event_page", None))
            and callable(getattr(reader, "history_stats", None))):
        return await _bounded_history(reader, thread_id)

    messages, events, goals, checkpoints = await asyncio.gather(
        reader.load_messages(thread_id),
        reader.load_events(thread_id),
        reader.list_goals(thread_id),
        reader.list_checkpoints(thread_id),
    )
    return RestoredThread(
        thread_id=thread_id,
        messages=tuple(messages),
        events=tuple(events),
        goals=tuple(goals),
        checkpoints=tuple(checkpoints),
    )


async def _bounded_history(reader, thread_id):
    stats, messages, events, goals, checkpoints = await asyncio.gather(
        reader.history_stats(thread_id),
        reader.load_context_messages(thread_id, limit=100, max_bytes=1048576),
        reader.read_event_page(thread_id, newest=True, limit=100, max_bytes=1048576),
        reader.list_goals(thread_id, limit=100, max_bytes=1048576),
        reader.list_checkpoints(thread_id, limit=100, max_bytes=1048576),
    )
    task = await reader.load_task_for_thread(thread_id)
    result = None
    if task is not None:
        # Project the current identity, even when its terminal event precedes
        # many later telemetry events outside the display page.
        from pathlib import Path
        from .task_controller import ForegroundTaskController
        result = await ForegroundTaskController(None, reader, Path.cwd()).result(task.id)
    return RestoredThread(thread_id, tuple(messages), tuple(row["event"] for row in events),
        tuple(goals), tuple(checkpoints), stats["message_count"], stats["event_count"],
        stats["message_count"] > len(messages) or stats["event_count"] > len(events), task, result)
