from __future__ import annotations

import asyncio
from typing import Awaitable, Protocol, Sequence

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message
from code_agent.thread_intelligence.compaction import SemanticCompactionResult


class SemanticCompactor(Protocol):
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
    ) -> SemanticCompactionResult: ...


async def _capture_async_cancellation(
    work: Awaitable[SemanticCompactionResult],
) -> SemanticCompactionResult | asyncio.CancelledError:
    try:
        return await work
    except asyncio.CancelledError as error:
        return error


async def compact_with_cancellation(
    compactor: SemanticCompactor,
    thread_id: str,
    revision: int,
    messages: Sequence[Message],
    *,
    context_tokens: int,
    context_limit: int,
    target_tokens: int,
    cancellation: CancellationToken,
) -> SemanticCompactionResult:
    cancellation.raise_if_cancelled()
    semantic_task = asyncio.create_task(
        _capture_async_cancellation(
            compactor.compact(
                thread_id,
                revision,
                messages,
                context_tokens=context_tokens,
                context_limit=context_limit,
                target_tokens=target_tokens,
                cancellation=cancellation,
            )
        )
    )
    cancellation_task = asyncio.create_task(cancellation.wait_async())
    tasks = (semantic_task, cancellation_task)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if cancellation_task in done:
            cancellation.raise_if_cancelled()
        result = await semantic_task
        if isinstance(result, asyncio.CancelledError):
            raise result
        cancellation.raise_if_cancelled()
        if not isinstance(result, SemanticCompactionResult):
            raise TypeError("semantic_compactor returned an invalid result")
        return result
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
