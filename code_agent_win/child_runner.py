from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from inspect import Parameter, signature

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentReference,
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
)


class EngineChildRunner:
    def __init__(
        self,
        engine_factory: Callable[
            [AgentDefinition], tuple[object, object | None]
        ],
        *,
        sessions: object | None = None,
        parent_thread: Callable[[], str] | None = None,
    ) -> None:
        self._factory = engine_factory
        self._sessions, self._parent_thread = sessions, parent_thread
        self._thread_listeners: set[Callable[[str, str], None]] = set()
        self._execution_context: ContextVar[ActionExecutionContext | None] = (
            ContextVar("child_execution_context", default=None)
        )

    def bind_execution_context(
        self, context: ActionExecutionContext | None
    ) -> Token[ActionExecutionContext | None]:
        if context is not None and not isinstance(context, ActionExecutionContext):
            raise TypeError("context must be an ActionExecutionContext or None")
        return self._execution_context.set(context)

    def reset_execution_context(
        self, token: Token[ActionExecutionContext | None]
    ) -> None:
        self._execution_context.reset(token)

    def subscribe_thread(
        self, listener: Callable[[str, str], None]
    ) -> Callable[[], None]:
        self._thread_listeners.add(listener)
        return lambda: self._thread_listeners.discard(listener)

    async def run(
        self, request: ChildRunRequest, cancellation: CancellationToken
    ) -> ChildRunResult:
        parent = self._execution_context.get()
        if request.agent.may_write and parent is None:
            return ChildRunResult(
                request.run_id,
                RunStatus.FAILED,
                "",
                error="writable child requires parent action lineage",
            )
        engine, closer = self._build_engine(request.agent, parent)
        started = time.monotonic()
        answers: list[str] = []
        tokens = 0
        tool_calls = 0
        references: list[AgentReference] = []
        try:
            thread_id = None
            if self._sessions is not None and self._parent_thread is not None:
                thread_id = await self._sessions.create_thread(
                    parent_thread_id=self._parent_thread()
                )
                for listener in tuple(self._thread_listeners):
                    listener(request.run_id, thread_id)
            run_options = {"cancellation": cancellation}
            if thread_id is not None:
                run_options["thread_id"] = thread_id
            async for event in engine.run(request.objective, **run_options):
                if event.kind is EventKind.RUN_STARTED:
                    value = event.payload.get("thread_id")
                    if isinstance(value, str):
                        references.append(AgentReference("thread", value))
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
                        if (
                            model_event.kind is ModelEventKind.USAGE
                            and model_event.usage
                        ):
                            tokens += model_event.usage.total_tokens
            summary = "\n\n".join(answers).strip()[:16_384]
            if not summary:
                summary = (
                    "Child run completed without a final advisory message."
                )
            return ChildRunResult(
                request.run_id,
                RunStatus.COMPLETED,
                summary,
                AgentUsage(
                    tokens, tool_calls, int(time.monotonic() - started)
                ),
                tuple(references),
            )
        finally:
            close = getattr(closer, "aclose", None)
            if close is not None:
                await close()

    def _build_engine(
        self, agent: AgentDefinition, parent: ActionExecutionContext | None
    ) -> tuple[object, object | None]:
        if _accepts_parent_context(self._factory):
            return self._factory(agent, parent)  # type: ignore[misc,call-arg]
        return self._factory(agent)  # type: ignore[misc,call-arg]


def _accepts_parent_context(factory: object) -> bool:
    try:
        parameters = tuple(signature(factory).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = (
        Parameter.POSITIONAL_ONLY,
        Parameter.POSITIONAL_OR_KEYWORD,
    )
    if any(parameter.kind is Parameter.VAR_POSITIONAL for parameter in parameters):
        return True
    return sum(parameter.kind in positional for parameter in parameters) >= 2
