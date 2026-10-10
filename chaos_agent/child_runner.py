from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar, Token
from inspect import Parameter, signature
from dataclasses import replace

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.task import TaskAuthorization, TaskStatus
from code_agent.orchestration.models import (
    AgentDefinition,
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
        thread_binding: object | None = None,
    ) -> None:
        self._factory = engine_factory
        self._sessions, self._parent_thread = sessions, parent_thread
        self._thread_binding = thread_binding
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
        authorization = await self._parent_authorization(request, parent)
        if request.required_sources:
            if authorization is None or self._sessions is None:
                raise ValueError('required sources require durable child authority')
            from .source_completion import canonical_sources
            sources = await asyncio.to_thread(canonical_sources, request.required_sources, authorization)
            request = replace(request, required_sources=sources)
        thread_id = None
        if self._sessions is not None:
            thread_id = await self._sessions.create_thread(parent_thread_id=parent.owner_thread_id)
            await self._sessions.bind_child_budget(thread_id, parent.owner_thread_id,
                parent.task_id, parent.request_id, max_total_tokens=request.token_budget,
                max_tool_calls=request.tool_budget, required_sources=request.required_sources)
        engine, closer = self._build_engine(request.agent, parent, authorization)
        if hasattr(engine, "_limits"):
            engine._limits = replace(engine._limits,
                max_total_tokens=min(engine._limits.max_total_tokens, request.token_budget),
                max_tool_calls=min(engine._limits.max_tool_calls, request.tool_budget))
        binding_token = None
        try:
            if thread_id is not None:
                for listener in tuple(self._thread_listeners):
                    listener(request.run_id, thread_id)
                if self._thread_binding is not None:
                    binding_token = self._thread_binding.bind(thread_id)
            run_options = {"cancellation": cancellation}
            if thread_id is not None:
                run_options["thread_id"] = thread_id
            if request.required_sources:
                run_options["required_sources"] = request.required_sources
            from .child_result import settled_child_result
            result = await settled_child_result(engine.run(request.objective, **run_options),
                request, cancellation, sessions=self._sessions)
        finally:
            try:
                from .child_result import settled_child_close
                if await settled_child_close(closer):
                    cancellation.cancel("child execution cancelled during close")
            finally:
                if binding_token is not None:
                    self._thread_binding.reset(binding_token)
        if cancellation.is_cancelled:
            return replace(result, status=RunStatus.CANCELLED, result=None, error=cancellation.reason)
        return result

    async def _parent_authorization(self, request, parent) -> TaskAuthorization | None:
        if self._sessions is None:
            return None
        if parent is None or parent.task_id is None:
            raise ValueError("production child requires concrete parent task lineage")
        task = await self._sessions.load_task(parent.task_id)
        if task.thread_id != parent.owner_thread_id:
            raise ValueError("child parent task belongs to another owner thread")
        if task.status not in {TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            raise ValueError("child parent task is not active")
        authorization = task.contract.authorization
        if not request.agent.may_write:
            authorization = replace(authorization, allow_workspace_write=False, allow_local_execute=False)
        return authorization

    def _build_engine(
        self, agent: AgentDefinition, parent: ActionExecutionContext | None,
        authorization: TaskAuthorization | None = None,
    ) -> tuple[object, object | None]:
        if _accepts_parent_context(self._factory, minimum=3):
            return self._factory(agent, parent, authorization)
        if _accepts_parent_context(self._factory):
            return self._factory(agent, parent)  # type: ignore[misc,call-arg]
        return self._factory(agent)  # type: ignore[misc,call-arg]


def _accepts_parent_context(factory: object, minimum: int = 2) -> bool:
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
    return sum(parameter.kind in positional for parameter in parameters) >= minimum
