from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from code_agent.context.attachment_budget import attachment_metadata, message_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, ToolDefinition
from code_agent.core.protocols import ContextBuilder
from code_agent.core.task_state import TaskState
from code_agent.sessions.models import MessageRecord

from .compaction import SemanticCompactor, checkpoint_message
from .models import (
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    ThreadEntry,
    anchor_message,
    source_range_digest,
)


class ThreadContextStore(Protocol):
    async def load_message_records(
        self, thread_id: str
    ) -> tuple[MessageRecord, ...]: ...

    async def publish_semantic_checkpoint(
        self, checkpoint: object, entries: Sequence[ThreadEntry]
    ) -> None: ...

    async def load_semantic_checkpoints(
        self, thread_id: str
    ) -> tuple[SemanticCheckpoint, ...]: ...


@dataclass(frozen=True)
class ManualCompactionReport:
    before_messages: int
    after_messages: int
    before_tokens: int
    after_tokens: int
    checkpoint_id: str | None
    fallback_used: bool


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
        thread_id: str | ContextRequest,
        messages: Sequence[Message] | None = None,
        user_input: str | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        task_state: TaskState | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ContextBundle:
        request = _resolve_request(
            thread_id, messages, user_input, tools, task_state, cancellation
        )
        thread_id = request.thread_id
        cancellation = request.cancellation
        cancellation.raise_if_cancelled()
        records = await self._store.load_message_records(thread_id)
        durable, sequences = await self._effective_messages(thread_id, records)
        result = await self._compactor.compact(
            thread_id,
            request.revision,
            durable,
            message_sequences=sequences,
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
        delegated = replace(
            request, messages=tuple(selected), user_input="", attachments=()
        )
        return await self._inner.build(delegated)

    async def compact_context(
        self,
        thread_id: str,
        cancellation: CancellationToken | None = None,
    ) -> ManualCompactionReport:
        """Force one semantic compaction attempt and persist its checkpoint."""
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        records = await self._store.load_message_records(thread_id)
        if not records:
            raise ValueError("the current thread has no messages to compact")
        messages, sequences = await self._effective_messages(thread_id, records)
        before_tokens = _message_tokens(messages)
        result = await self._compactor.compact(
            thread_id,
            max(1, records[-1].sequence),
            messages,
            message_sequences=sequences,
            context_tokens=self._context_limit,
            context_limit=self._context_limit,
            target_tokens=self._target_tokens,
            cancellation=token,
        )
        if result.checkpoint is not None:
            await self._store.publish_semantic_checkpoint(
                result.checkpoint,
                _index_entries(records, result.checkpoint.id, result.checkpoint.summary),
            )
        return ManualCompactionReport(
            len(messages),
            len(result.messages),
            before_tokens,
            _message_tokens(result.messages),
            result.checkpoint.id if result.checkpoint else None,
            result.fallback_used,
        )

    async def _effective_messages(
        self, thread_id: str, records: Sequence[MessageRecord]
    ) -> tuple[tuple[Message, ...], tuple[int, ...]]:
        messages = tuple(record.message for record in records)
        sequences = tuple(record.sequence for record in records)
        loader = getattr(self._store, "load_semantic_checkpoints", None)
        if not callable(loader):
            return messages, sequences
        checkpoints = await loader(thread_id)
        applied = _apply_checkpoints(records, checkpoints)
        return applied or (messages, sequences)


def _resolve_request(
    thread_id: str | ContextRequest,
    messages: Sequence[Message] | None,
    user_input: str | None,
    tools: Sequence[ToolDefinition] | None,
    task_state: TaskState | None,
    cancellation: CancellationToken | None,
) -> ContextRequest:
    if isinstance(thread_id, ContextRequest):
        return thread_id
    if messages is None or user_input is None or tools is None:
        raise TypeError("legacy context arguments are incomplete")
    if task_state is None or cancellation is None:
        raise TypeError("task_state and cancellation are required")
    return ContextRequest(
        thread_id=thread_id,
        revision=1,
        messages=tuple(messages),
        user_input=user_input,
        tools=tuple(tools),
        task_state=task_state,
        cancellation=cancellation,
    )


def _message_tokens(messages: Sequence[Message]) -> int:
    return sum(message_tokens(message) for message in messages)


def _index_entries(
    records: Sequence[MessageRecord], checkpoint_id: str, summary: str
) -> tuple[ThreadEntry, ...]:
    if not records:
        return ()
    source_entries = tuple(
        ThreadEntry(
            anchor_message(record.thread_id, record.sequence, record.message).anchor,
            _indexable_message(record.message),
        )
        for record in records
        if record.message.content.strip() or record.message.attachments
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


def _indexable_message(message: Message) -> str:
    metadata = "; ".join(
        attachment_metadata(item) for item in message.attachments
    )
    sections = [message.content.strip()]
    if metadata:
        sections.append(f"Attachments: {metadata}")
    return "\n".join(section for section in sections if section)


def _apply_checkpoints(
    records: Sequence[MessageRecord], checkpoints: Sequence[SemanticCheckpoint]
) -> tuple[tuple[Message, ...], tuple[int, ...]] | None:
    selected: list[SemanticCheckpoint] = []
    occupied: set[int] = set()
    for checkpoint in reversed(checkpoints):
        covered = set(range(
            checkpoint.source_start.sequence, checkpoint.source_end.sequence + 1
        ))
        if covered.isdisjoint(occupied) and _checkpoint_is_valid(records, checkpoint):
            selected.append(checkpoint)
            occupied.update(covered)
    if not selected:
        return None
    by_start = {item.source_start.sequence: item for item in selected}
    messages: list[Message] = []
    sequences: list[int] = []
    for record in records:
        checkpoint = by_start.get(record.sequence)
        if checkpoint is not None:
            messages.append(checkpoint_message(checkpoint))
            sequences.append(record.sequence)
        if record.sequence not in occupied:
            messages.append(record.message)
            sequences.append(record.sequence)
    return tuple(messages), tuple(sequences)


def _checkpoint_is_valid(
    records: Sequence[MessageRecord], checkpoint: SemanticCheckpoint
) -> bool:
    selected = tuple(
        record
        for record in records
        if checkpoint.source_start.sequence
        <= record.sequence
        <= checkpoint.source_end.sequence
    )
    if not selected:
        return False
    anchored = tuple(
        anchor_message(record.thread_id, record.sequence, record.message)
        for record in selected
    )
    if (
        anchored[0].anchor.stable_id != checkpoint.source_start.stable_id
        or anchored[-1].anchor.stable_id != checkpoint.source_end.stable_id
        or anchored[0].anchor.digest != checkpoint.source_start.digest
        or anchored[-1].anchor.digest != checkpoint.source_end.digest
        or source_range_digest(anchored) != checkpoint.source_digest
    ):
        return False
    return True
