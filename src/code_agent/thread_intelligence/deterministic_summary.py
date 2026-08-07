from __future__ import annotations

import asyncio

from code_agent.context.tokens import truncate_to_tokens
from code_agent.context.attachment_budget import attachment_metadata
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Usage

from .models import AnchoredMessage, SummaryRequest, SummaryResponse


_CHECKPOINT_HEADER = "Untrusted conversation checkpoint:"
_SOURCE_SNIPPET_TOKENS = 12


def render_bounded_source_summary(
    sources: tuple[AnchoredMessage, ...], max_tokens: int
) -> str:
    """Render source-ordered untrusted text within a deterministic token bound."""
    if not isinstance(sources, tuple):
        raise TypeError("sources must be a tuple")
    if not sources:
        raise ValueError("sources must not be empty")
    if any(not isinstance(source, AnchoredMessage) for source in sources):
        raise TypeError("sources must contain AnchoredMessage values")
    if len({source.anchor.thread_id for source in sources}) != 1:
        raise ValueError("summary sources must belong to one thread")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise TypeError("max_tokens must be an integer")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    lines = [_CHECKPOINT_HEADER]
    for source in sources:
        message = source.message
        fields = [
            f"- source={source.anchor.stable_id}",
            f"role={message.role}",
        ]
        if message.role == "assistant" and message.tool_calls:
            fields.append(
                "action=" + ",".join(call.name for call in message.tool_calls)
            )
        collapsed = " ".join(message.content.split())
        snippet = truncate_to_tokens(collapsed, _SOURCE_SNIPPET_TOKENS)
        fields.append(f"content={snippet}")
        if message.attachments:
            fields.append(
                "attachments="
                + ";".join(
                    attachment_metadata(item) for item in message.attachments
                )
            )
        lines.append(" ".join(fields))
    return truncate_to_tokens("\n".join(lines), max_tokens)


class DeterministicSummaryService:
    """Summarize anchored messages locally without provider access or usage."""

    async def summarize(
        self, request: SummaryRequest, cancellation: CancellationToken
    ) -> SummaryResponse:
        if not isinstance(request, SummaryRequest):
            raise TypeError("request must be SummaryRequest")
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be CancellationToken")
        cancellation.raise_if_cancelled()
        summary = await asyncio.to_thread(
            render_bounded_source_summary,
            request.sources,
            request.max_output_tokens,
        )
        cancellation.raise_if_cancelled()
        return SummaryResponse(
            summary,
            "deterministic-anchor-v1",
            Usage(),
        )
