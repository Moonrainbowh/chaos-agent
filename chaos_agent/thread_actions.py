from __future__ import annotations

from collections.abc import Callable, Mapping

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.thread_intelligence.models import SourceAnchor, SourceKind
from code_agent.thread_intelligence.tools import ThreadIntelligenceTools


async def execute_thread_action(
    request: ActionRequest,
    tools: ThreadIntelligenceTools,
    caller_thread: Callable[[], str],
) -> ActionResult:
    arguments = request.arguments
    if request.name == "search_threads":
        hits = await tools.search(
            caller_thread(),
            _text(arguments, "query"),
            thread_id=arguments.get("thread_id"),
            limit=int(arguments.get("limit", 20)),
        )
        return ActionResult(
            request.id,
            request.name,
            {
                "hits": [
                    {
                        "thread_id": hit.entry.anchor.thread_id,
                        "kind": hit.entry.anchor.kind.value,
                        "sequence": hit.entry.anchor.sequence,
                        "stable_id": hit.entry.anchor.stable_id,
                        "digest": hit.entry.anchor.digest,
                        "text": hit.entry.text,
                        "score": hit.score,
                    }
                    for hit in hits
                ]
            },
        )
    read = await tools.read(
        caller_thread(),
        SourceAnchor(
            _text(arguments, "thread_id"),
            SourceKind(_text(arguments, "kind")),
            int(arguments["sequence"]),
            _text(arguments, "stable_id"),
            _text(arguments, "digest"),
        ),
    )
    return ActionResult(
        request.id,
        request.name,
        {
            "primary": read.primary.text,
            "later_revisions": [item.text for item in read.later_revisions],
            "tool_outcomes": [item.text for item in read.tool_outcomes],
            "conflict_candidate": read.conflict_candidate,
        },
    )


def _text(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value
