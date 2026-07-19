from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
)


class EngineChildRunner:
    def __init__(
        self,
        engine_factory: Callable[[AgentDefinition], tuple[object, object | None]],
    ) -> None:
        self._factory = engine_factory

    async def run(
        self,
        request: ChildRunRequest,
        cancellation: CancellationToken,
    ) -> ChildRunResult:
        engine, closer = self._factory(request.agent)
        started = time.monotonic()
        answers: list[str] = []
        tokens = 0
        tool_calls = 0
        references = []
        try:
            async for event in engine.run(request.objective, cancellation=cancellation):
                if event.kind is EventKind.RUN_STARTED:
                    thread_id = event.payload.get("thread_id")
                    if isinstance(thread_id, str):
                        from code_agent.orchestration.models import AgentReference

                        references.append(AgentReference("thread", thread_id))
                elif event.kind is EventKind.ACTION_REQUESTED:
                    tool_calls += 1
                elif event.kind is EventKind.MESSAGE_ADDED:
                    raw = event.payload.get("message")
                    if isinstance(raw, Mapping):
                        message = Message.from_dict(raw)
                        if message.role == "assistant" and message.content:
                            answers.append(message.content)
                elif event.kind is EventKind.MODEL_EVENT:
                    raw = event.payload.get("event")
                    if isinstance(raw, Mapping):
                        model_event = ModelEvent.from_dict(raw)
                        if model_event.kind is ModelEventKind.USAGE and model_event.usage:
                            tokens += model_event.usage.total_tokens
            summary = "\n\n".join(answers).strip()[:16_384]
            if not summary:
                summary = "Child run completed without a final advisory message."
            return ChildRunResult(
                request.run_id,
                RunStatus.COMPLETED,
                summary,
                AgentUsage(tokens, tool_calls, int(time.monotonic() - started)),
                tuple(references),
            )
        finally:
            close = getattr(closer, "aclose", None)
            if close is not None:
                await close()


__all__ = ["EngineChildRunner"]
