from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token

from code_agent.core.action_execution import ActionExecutionContext
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
        engine_factory: Callable[
            [AgentDefinition, ActionExecutionContext | None],
            tuple[object, object | None],
        ],
    ) -> None:
        self._factory = engine_factory
        self._execution_context: ContextVar[ActionExecutionContext | None] = (
            ContextVar("child_execution_context", default=None)
        )

    def bind_execution_context(
        self, context: ActionExecutionContext | None
    ) -> Token[ActionExecutionContext | None]:
        if context is not None and not isinstance(
            context, ActionExecutionContext
        ):
            raise TypeError("context must be an ActionExecutionContext or None")
        return self._execution_context.set(context)

    def reset_execution_context(
        self, token: Token[ActionExecutionContext | None]
    ) -> None:
        self._execution_context.reset(token)

    async def run(
        self,
        request: ChildRunRequest,
        cancellation: CancellationToken,
    ) -> ChildRunResult:
        parent = self._execution_context.get()
        if request.agent.may_write and parent is None:
            return ChildRunResult(
                request.run_id,
                RunStatus.FAILED,
                "",
                error="writable child requires parent action lineage",
            )
        engine, closer = self._factory(request.agent, parent)
        try:
            return await self._collect(engine, request, cancellation)
        finally:
            close = getattr(closer, "aclose", None)
            if close is not None:
                await close()

    async def _collect(
        self,
        engine: object,
        request: ChildRunRequest,
        cancellation: CancellationToken,
    ) -> ChildRunResult:
        started = time.monotonic()
        answers: list[str] = []
        tokens = 0
        tool_calls = 0
        references = []
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


__all__ = ["EngineChildRunner"]
