from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, Sequence

from code_agent.core.events import AgentEvent
from code_agent.core.models import Message
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


async def load_thread_history(
    reader: ThreadHistoryReader, thread_id: str
) -> RestoredThread:
    """Load every persisted collection for a thread without changing storage."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be non-blank text")

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
