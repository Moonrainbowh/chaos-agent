from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolDefinition,
)
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
    RunView,
)
from code_agent.orchestration.budget import BudgetLedger, ParentBudget
from code_agent.orchestration.modes import ModeRegistry
from code_agent.orchestration.supervisor import ChildRunSupervisor
from code_agent.orchestration.supervisor import ChildRunSupervisor
from code_agent.providers.config import ModelProfile


_READ_ONLY_ROLES = {
    AgentRole.ORACLE,
    AgentRole.REVIEW,
    AgentRole.SEARCH,
    AgentRole.LIBRARIAN,
}


class RestrictedDispatcher:
    def __init__(self, inner: object, allowed_tools: Sequence[str]) -> None:
        self._inner = inner
        self._allowed = frozenset(allowed_tools) - {"delegate_agent"}

    def tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool for tool in self._inner.tools() if tool.name in self._allowed)

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: object = None,
    ) -> ActionResult:
        if request.name not in self._allowed:
            return ActionResult(
                request.id,
                request.name,
                {"error": "child tool is outside its mode and role"},
                is_error=True,
            )
        return await self._inner.dispatch(request, cancellation, task_authorization)


class EngineChildRunner:
    def __init__(self, engine_factory: Callable[[AgentDefinition], tuple[object, object | None]]) -> None:
        self._factory = engine_factory

    async def run(
        self, request: ChildRunRequest, cancellation: CancellationToken
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


class SubagentTool:
    def __init__(
        self,
        supervisor: ChildRunSupervisor,
        mode_registry: ModeRegistry,
        profiles: dict[str, ModelProfile],
        default_mode: AgentMode = AgentMode.MEDIUM,
    ) -> None:
        self._supervisor = supervisor
        self._modes = mode_registry
        self._profiles = profiles
        self._default_mode = default_mode

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        arguments = request.arguments
        role = AgentRole(str(arguments["role"]))
        snapshot = self._modes.freeze(self._default_mode, self._profiles)
        allowed = snapshot.definition.tool_names
        may_write = role is AgentRole.SUBAGENT and any(
            name in {"write_file", "replace_text", "run_verification", "run_command"}
            for name in allowed
        )
        if role in _READ_ONLY_ROLES:
            allowed = tuple(name for name in allowed if name in {"read_file", "list_files", "search_text", "git_status", "git_diff"})
        agent = AgentDefinition(
            f"{role.value}-{request.id[:16]}",
            role,
            snapshot,
            _instructions(role),
            tuple(allowed),
            may_write=may_write,
        )
        child = ChildRunRequest(
            request.id,
            str(arguments["objective"]),
            agent,
            1,
            int(arguments.get("token_budget", 20_000)),
            int(arguments.get("tool_budget", 16)),
            int(arguments.get("active_seconds", 300)),
        )
        result = await self._supervisor.run(child)
        return ActionResult(
            request.id,
            request.name,
            {
                "advisory": True,
                "run_id": result.run_id,
                "role": role.value,
                "status": result.status.value,
                "summary": result.summary,
                "usage": {
                    "tokens": result.usage.total_tokens,
                    "tool_calls": result.usage.tool_calls,
                    "active_seconds": result.usage.active_seconds,
                },
                "references": [reference.identifier for reference in result.references],
                "error": result.error,
            },
            is_error=result.status is not RunStatus.COMPLETED,
        )


def _instructions(role: AgentRole) -> str:
    return {
        AgentRole.SUBAGENT: "Complete the bounded objective and report evidence. Do not claim parent completion.",
        AgentRole.ORACLE: "Analyze the difficult question and return advisory reasoning with source references.",
        AgentRole.REVIEW: "Review for defects, regressions, and missing tests. Return findings first.",
        AgentRole.SEARCH: "Search the authorized workspace and return concise source-grounded results.",
        AgentRole.LIBRARIAN: "Locate and organize relevant project knowledge with stable references.",
    }[role]


class SubagentRuntime:
    """Keep one cumulative child budget per foreground parent task."""

    def __init__(
        self,
        runner: EngineChildRunner,
        mode_registry: ModeRegistry,
        profiles: dict[str, ModelProfile],
        default_child_mode: AgentMode = AgentMode.MEDIUM,
        budget: ParentBudget = ParentBudget(),
    ) -> None:
        self._runner = runner
        self._modes = mode_registry
        self._profiles = profiles
        self._default_child_mode = default_child_mode
        self._budget = budget
        self._parent: ContextVar[str] = ContextVar("subagent_parent", default="adhoc")
        self._supervisors: dict[str, ChildRunSupervisor] = {}
        self._listeners: set[Callable[[RunView], None]] = set()

    def subscribe(self, listener: Callable[[RunView], None]) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _publish(self, view: RunView) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(view)
            except Exception:
                continue

    def activate(self, parent_id: str) -> Token[str]:
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError("parent_id must be non-blank")
        return self._parent.set(parent_id)

    def reset(self, token: Token[str]) -> None:
        self._parent.reset(token)

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        parent_id = self._parent.get()
        supervisor = self._supervisors.get(parent_id)
        if supervisor is None:
            supervisor = ChildRunSupervisor(
                self._runner, BudgetLedger(self._budget), cancellation
            )
            supervisor.subscribe(self._publish)
            self._supervisors[parent_id] = supervisor
        return await SubagentTool(
            supervisor,
            self._modes,
            self._profiles,
            self._default_child_mode,
        ).dispatch(request, cancellation)

    async def release(self, parent_id: str) -> None:
        supervisor = self._supervisors.pop(parent_id, None)
        if supervisor is not None:
            await supervisor.wait_all()

    async def aclose(self) -> None:
        supervisors = tuple(self._supervisors.values())
        for supervisor in supervisors:
            await supervisor.cancel_all("application closing")
        for supervisor in supervisors:
            await supervisor.wait_all()
        self._supervisors.clear()
