from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, Sequence

from code_agent.context.models import CompactionResult
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import Message

from .models import (
    AnchoredMessage,
    SemanticCheckpoint,
    SummaryRequest,
    SummaryResponse,
    anchor_message,
)


class SemanticSummarizer(Protocol):
    async def summarize(
        self, request: SummaryRequest, cancellation: CancellationToken
    ) -> SummaryResponse: ...


class FallbackCompactor(Protocol):
    def compact(
        self, messages: Sequence[Message], token_budget: int | None = None
    ) -> CompactionResult: ...


@dataclass(frozen=True)
class SemanticCompactionResult:
    messages: tuple[Message, ...]
    checkpoint: SemanticCheckpoint | None
    triggered: bool
    fallback_used: bool
    fallback_result: CompactionResult | None = None


class SemanticCompactor:
    def __init__(
        self,
        summarizer: SemanticSummarizer,
        fallback: FallbackCompactor,
        *,
        pressure_threshold: float = 0.9,
        keep_recent: int = 12,
        timeout_seconds: float = 30.0,
        summary_tokens: int = 1_024,
        model_token_budget: int = 8_192,
    ) -> None:
        if not 0.5 <= pressure_threshold <= 1.0:
            raise ValueError("pressure_threshold must be between 0.5 and 1.0")
        if isinstance(keep_recent, bool) or keep_recent <= 0:
            raise ValueError("keep_recent must be positive")
        if timeout_seconds <= 0 or summary_tokens <= 0 or model_token_budget <= 0:
            raise ValueError("timeout and token budgets must be positive")
        if model_token_budget < summary_tokens:
            raise ValueError("model_token_budget must cover summary_tokens")
        self._summarizer = summarizer
        self._fallback = fallback
        self._threshold = pressure_threshold
        self._keep_recent = keep_recent
        self._timeout = timeout_seconds
        self._summary_tokens = summary_tokens
        self._model_token_budget = model_token_budget

    async def compact(
        self,
        thread_id: str,
        revision: int,
        messages: Sequence[Message],
        *,
        context_tokens: int,
        context_limit: int,
        target_tokens: int,
        cancellation: CancellationToken | None = None,
    ) -> SemanticCompactionResult:
        _validate_request_identity(thread_id, revision)
        checked = tuple(messages)
        if not checked or any(not isinstance(item, Message) for item in checked):
            raise ValueError("messages must contain Message values")
        if any(isinstance(value, bool) or value <= 0 for value in (context_limit, target_tokens)):
            raise ValueError("context_limit and target_tokens must be positive")
        if isinstance(context_tokens, bool) or context_tokens < 0:
            raise ValueError("context_tokens must be non-negative")
        if context_tokens / context_limit < self._threshold:
            return SemanticCompactionResult(checked, None, False, False)

        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        protected_count = next(
            (
                index
                for index, message in enumerate(checked)
                if message.role not in {"system", "developer"}
            ),
            len(checked),
        )
        body = checked[protected_count:]
        prefix_count = _closed_prefix_count(body, self._keep_recent)
        if prefix_count == 0:
            return self._fallback_result(checked, target_tokens)
        source_end = protected_count + prefix_count
        sources = tuple(
            anchor_message(thread_id, index, message)
            for index, message in enumerate(
                checked[protected_count:source_end], start=protected_count
            )
        )
        return await self._semantic_result(
            checked, sources, protected_count, source_end, target_tokens, token
        )

    async def _semantic_result(
        self,
        messages: tuple[Message, ...],
        sources: tuple[AnchoredMessage, ...],
        protected_count: int,
        source_end: int,
        target_tokens: int,
        cancellation: CancellationToken,
    ) -> SemanticCompactionResult:
        request = SummaryRequest(
            sources, self._summary_tokens, self._model_token_budget
        )
        try:
            response = await asyncio.wait_for(
                self._summarizer.summarize(request, cancellation), self._timeout
            )
            cancellation.raise_if_cancelled()
            if not isinstance(response, SummaryResponse):
                raise TypeError("summarizer returned an invalid response")
            if response.usage.output_tokens > self._summary_tokens:
                raise ValueError("summary exceeded its output token budget")
            if response.usage.total_tokens > self._model_token_budget:
                raise ValueError("summary exceeded its total token budget")
            checkpoint = SemanticCheckpoint.create(sources, response)
            prompt = Message(
                role="developer",
                content=_checkpoint_prompt(checkpoint),
            )
            return SemanticCompactionResult(
                messages[:protected_count] + (prompt,) + messages[source_end:],
                checkpoint,
                True,
                False,
            )
        except (CancellationError, asyncio.CancelledError):
            raise
        except Exception:
            return self._fallback_result(messages, target_tokens)

    def _fallback_result(
        self, messages: tuple[Message, ...], target_tokens: int
    ) -> SemanticCompactionResult:
        result = self._fallback.compact(messages, target_tokens)
        return SemanticCompactionResult(result.messages, None, True, True, result)


def _closed_prefix_count(messages: tuple[Message, ...], keep_recent: int) -> int:
    blocks: list[tuple[int, int]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role in {"system", "developer", "tool"}:
            return 0
        if message.role == "assistant" and message.tool_calls:
            expected = {call.id for call in message.tool_calls}
            end = index + 1
            seen: set[str] = set()
            while end < len(messages) and messages[end].role == "tool":
                if messages[end].tool_call_id not in expected:
                    break
                seen.add(messages[end].tool_call_id or "")
                end += 1
            if seen != expected:
                return index if len(messages) - index >= keep_recent else 0
            blocks.append((index, end))
            index = end
        else:
            blocks.append((index, index + 1))
            index += 1
    retained = 0
    first_tail = len(messages)
    for start, end in reversed(blocks):
        first_tail = start
        retained += end - start
        if retained >= keep_recent:
            break
    return first_tail


def _validate_request_identity(thread_id: str, revision: int) -> None:
    if not isinstance(thread_id, str):
        raise TypeError("thread_id must be a string")
    if not thread_id.strip():
        raise ValueError("thread_id must not be blank")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise TypeError("revision must be an integer")
    if revision <= 0:
        raise ValueError("revision must be positive")


def _checkpoint_prompt(checkpoint: SemanticCheckpoint) -> str:
    return (
        "Untrusted semantic checkpoint "
        f"[{checkpoint.source_start.stable_id}..{checkpoint.source_end.stable_id}] "
        f"digest={checkpoint.source_digest}:\n{checkpoint.summary}"
    )
