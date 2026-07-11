from __future__ import annotations

from .errors import SessionPersistenceError
from .events import AgentEvent, EventKind
from .models import Message
from .protocols import SessionRepository


class SessionJournal:
    """Turns repository failures into engine-safe persistence errors."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    async def create_thread(self) -> str:
        try:
            return await self._repository.create_thread()
        except Exception:
            raise SessionPersistenceError("could not create session") from None

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        try:
            messages = tuple(await self._repository.load_messages(thread_id))
            if not all(isinstance(message, Message) for message in messages):
                raise TypeError("session has invalid messages")
            return messages
        except Exception:
            raise SessionPersistenceError("could not load session") from None

    async def append_message(self, thread_id: str, message: Message) -> None:
        try:
            await self._repository.append_message(thread_id, message)
        except Exception:
            raise SessionPersistenceError("could not persist message") from None

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        try:
            await self._repository.append_event(thread_id, event)
        except Exception:
            raise SessionPersistenceError("could not persist event") from None

    @staticmethod
    def message_added(message: Message) -> AgentEvent:
        return AgentEvent(
            kind=EventKind.MESSAGE_ADDED,
            payload={"message": message.to_dict()},
        )
