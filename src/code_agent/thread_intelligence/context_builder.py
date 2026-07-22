from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ContextBundle, Message, ToolDefinition
from code_agent.core.protocols import ContextBuilder
from code_agent.core.task_state import TaskState
from code_agent.sessions.models import MessageRecord

from .compaction import SemanticCompactor
from .models import SourceAnchor, SourceKind, ThreadEntry, anchor_message


class ThreadContextStore(Protocol):
    async def load_message_records(
        self, thread_id: str
    ) -> tuple[MessageRecord, ...]: ...

    async def publish_semantic_checkpoint(
        self, checkpoint: object, entries: Sequence[ThreadEntry]
    ) -> None: ...


class ThreadAwareContextBuilder:
    """Build context from durable messages and publish usable semantic summaries."""

    def __init__(
        self,
        store: ThreadContextStore,
        compactor: SemanticCompactor,
        inner: ContextBuilder,
        *,
        context_limit: int,
        target_tokens: int,
    ) -> None:
        if not isinstance(compactor, SemanticCompactor):
            raise TypeError("compactor must be a SemanticCompactor")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (context_limit, target_tokens)
        ):
            raise ValueError("context limits must be positive integers")
        self._store = store
        self._compactor = compactor
        self._inner = inner
        self._context_limit = context_limit
        self._target_tokens = target_tokens

    async def build(
        self,
        thread_id: str,
        messages: Sequence[Message],
        user_input: str,
        tools: Sequence[ToolDefinition],
        task_state: TaskState,
        cancellation: CancellationToken,
    ) -> ContextBundle:
        cancellation.raise_if_cancelled()
        records = await self._store.load_message_records(thread_id)
        durable = tuple(record.message for record in records)
        result = await self._compactor.compact(
            thread_id,
            durable,
            message_sequences=tuple(record.sequence for record in records),
            context_tokens=_message_tokens(durable),
            context_limit=self._context_limit,
            target_tokens=self._target_tokens,
            cancellation=cancellation,
        )
        selected = result.messages
        if result.checkpoint is not None:
            try:
                await self._store.publish_semantic_checkpoint(
                    result.checkpoint,
                    _index_entries(records, result.checkpoint.id, result.checkpoint.summary),
                )
            except Exception:
                selected = durable
        cancellation.raise_if_cancelled()
        return await self._inner.build(
            thread_id,
            selected,
            "",
            tools,
            task_state,
            cancellation,
        )


def _message_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_tokens(message.content) + 4 for message in messages)


def _index_entries(
    records: Sequence[MessageRecord], checkpoint_id: str, summary: str
) -> tuple[ThreadEntry, ...]:
    if not records:
        return ()
    source_entries = tuple(
        ThreadEntry(
            anchor_message(record.thread_id, record.sequence, record.message).anchor,
            record.message.content,
        )
        for record in records
        if record.message.content.strip()
    )
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    checkpoint_anchor = SourceAnchor(
        records[0].thread_id,
        SourceKind.CHECKPOINT,
        records[-1].sequence,
        checkpoint_id,
        digest,
    )
    return source_entries + (ThreadEntry(checkpoint_anchor, summary),)
