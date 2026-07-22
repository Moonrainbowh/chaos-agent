from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from code_agent.core.models import ToolDefinition

from .authorization import ThreadAuthorization
from .index import BoundedThreadIndex
from .models import SearchHit, SourceAnchor, ThreadEntry, ThreadRead
from .reader import ThreadReader


class ThreadToolStore(Protocol):
    async def search_thread_index(
        self, thread_ids: Iterable[str], query: str, *, limit: int = 20
    ) -> tuple[SearchHit, ...]: ...

    async def load_thread_entries(
        self, thread_ids: Iterable[str]
    ) -> tuple[ThreadEntry, ...]: ...


class ThreadIntelligenceTools:
    """Expose thread search/read while keeping caller identity Host-owned."""

    def __init__(
        self, store: ThreadToolStore, authorization: ThreadAuthorization
    ) -> None:
        self._store = store
        self._authorization = authorization

    def definitions(self) -> tuple[ToolDefinition, ToolDefinition]:
        return (_search_definition(), _read_definition())

    async def search(
        self,
        caller_thread_id: str,
        query: str,
        *,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> tuple[SearchHit, ...]:
        authorized = await self._authorization.authorized_threads(caller_thread_id)
        selected: Iterable[str] = authorized
        if thread_id is not None:
            await self._authorization.ensure_can_read(caller_thread_id, thread_id)
            selected = (thread_id,)
        return await self._store.search_thread_index(
            selected, query, limit=limit
        )

    async def read(
        self, caller_thread_id: str, anchor: SourceAnchor
    ) -> ThreadRead:
        await self._authorization.ensure_can_read(
            caller_thread_id, anchor.thread_id
        )
        authorized = await self._authorization.authorized_threads(caller_thread_id)
        entries = await self._store.load_thread_entries(authorized)
        index = BoundedThreadIndex(
            caller_thread_id,
            authorized - {caller_thread_id},
            capacity=max(1, len(entries)),
        )
        for entry in entries:
            index.add(entry)
        return ThreadReader(index).read_thread(anchor)


def _search_definition() -> ToolDefinition:
    return ToolDefinition(
        "search_threads",
        "Search bounded, authorized thread history and semantic checkpoints.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 512},
                "thread_id": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def _read_definition() -> ToolDefinition:
    return ToolDefinition(
        "read_thread",
        "Read one stable authorized thread source and later corrections.",
        {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": ["message", "event", "checkpoint", "evidence"],
                },
                "sequence": {"type": "integer", "minimum": 0},
                "stable_id": {"type": "string", "minLength": 1},
                "digest": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            },
            "required": ["thread_id", "kind", "sequence", "stable_id", "digest"],
            "additionalProperties": False,
        },
    )
