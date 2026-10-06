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

from .compaction import SemanticCompactor
from .history_context import SemanticHistory, SemanticHistoryCapacityError
from .models import (
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    ThreadEntry,
    anchor_message,
)


class ThreadContextStore(Protocol):
    async def read_history_page(self, thread_id: str, **kwargs) -> tuple[MessageRecord, ...]: ...

    async def history_stats(self, thread_id: str) -> dict: ...

    async def semantic_checkpoint_page(self, thread_id: str, **kwargs) -> tuple[SemanticCheckpoint, ...]: ...

    async def publish_semantic_checkpoint(
        self, checkpoint: object, entries: Sequence[ThreadEntry]
    ) -> None: ...



@dataclass(frozen=True)
class ManualCompactionReport:
    before_messages: int
    after_messages: int
    before_tokens: int
    after_tokens: int
    checkpoint_id: str | None
    fallback_used: bool
    migrated: bool = False
    checkpoints_published: int = 0


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
        self._history = SemanticHistory(store)

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
        preflight = getattr(self._inner, "preflight", None)
        if callable(preflight):
            await preflight(request)
        records, durable, sequences, _ = await self._history.load(thread_id)
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
        stats = await self._store.history_stats(thread_id)
        preflight = getattr(self._inner, "preflight", None)
        if callable(preflight):
            await preflight(ContextRequest(thread_id, max(1, stats["message_sequence"]),
                (), "", (), TaskState.empty(), token))
        try:
            records, messages, sequences, stats = await self._history.load(thread_id)
        except SemanticHistoryCapacityError:
            return await self._migrate_history(thread_id, token)
        if not messages:
            raise ValueError("the current thread has no messages to compact")
        before_tokens = _message_tokens(messages)
        result = await self._compactor.compact(
            thread_id,
            max(1, stats["message_sequence"]),
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

    async def _migrate_history(self, thread_id, token):
        """Explicit command only: reuse semantic compaction on closed bounded source batches."""
        initial = await self._store.history_stats(thread_id)
        before_tokens, cursor = 0, 0
        while True:
            token.raise_if_cancelled()
            page = await self._store.read_history_page(thread_id, after_sequence=cursor,
                before_sequence=initial["message_sequence"] + 1, limit=64,
                max_bytes=self._history.max_bytes)
            if not page:
                break
            before_tokens += _message_tokens(tuple(r.message for r in page))
            cursor = page[-1].sequence
        if (await self._store.history_stats(thread_id))["message_revision"] != initial["message_revision"]:
            raise ValueError("history changed before explicit semantic migration")
        published, last_id = 0, None
        while True:
            token.raise_if_cancelled()
            try:
                records, messages, sequences, stats = await self._history.load(thread_id)
                final = True
            except SemanticHistoryCapacityError:
                records, stats = await self._history.migration_batch(thread_id)
                messages = tuple(r.message for r in records)
                sequences = tuple(r.sequence for r in records)
                final = False
            result = await self._compactor.compact(thread_id, max(1, stats["message_sequence"]),
                messages, message_sequences=sequences, context_tokens=self._context_limit,
                context_limit=self._context_limit, target_tokens=self._target_tokens, cancellation=token)
            token.raise_if_cancelled()
            checkpoint = result.checkpoint
            if checkpoint is None:
                raise ValueError("explicit semantic migration produced no durable checkpoint; published prefix retained for retry")
            if not final and checkpoint.source_end.sequence < records[0].sequence:
                raise ValueError("semantic migration did not advance the uncovered source")
            if (await self._store.history_stats(thread_id))["message_revision"] != stats["message_revision"]:
                raise ValueError("history changed during explicit semantic migration; retry required")
            await self._store.publish_semantic_checkpoint(checkpoint,
                _index_entries(records, checkpoint.id, checkpoint.summary))
            published += 1
            last_id = checkpoint.id
            if final:
                return ManualCompactionReport(initial["message_count"], len(result.messages),
                    before_tokens, _message_tokens(result.messages), last_id, False, True, published)


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
