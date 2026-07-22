from __future__ import annotations

from typing import Protocol

from code_agent.sessions.models import ThreadRelation

from .index import ThreadAccessError


class ThreadRelationStore(Protocol):
    async def load_thread_relation(self, thread_id: str) -> ThreadRelation: ...


class ThreadAuthorization:
    """Resolve read scope solely from the persisted two-level thread tree."""

    def __init__(self, store: ThreadRelationStore) -> None:
        self._store = store

    async def authorized_threads(self, caller_thread_id: str) -> frozenset[str]:
        relation = await self._store.load_thread_relation(
            _thread_id(caller_thread_id, "caller_thread_id")
        )
        if relation.parent_thread_id is None:
            return frozenset((relation.thread_id, *relation.child_thread_ids))
        return frozenset((relation.thread_id, relation.parent_thread_id))

    async def ensure_can_read(
        self, caller_thread_id: str, target_thread_id: str
    ) -> None:
        target = _thread_id(target_thread_id, "target_thread_id")
        if target not in await self.authorized_threads(caller_thread_id):
            raise ThreadAccessError("thread is outside the authorized thread tree")


def _thread_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    return value
