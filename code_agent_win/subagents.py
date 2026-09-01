from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
)
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    ChildRunRequest,
    ChildRunResult,
    ModeSnapshot,
    RunStatus,
    RunView,
)
from code_agent.orchestration.budget import BudgetLedger, ParentBudget
from code_agent.orchestration.modes import ModeRegistry
from code_agent.orchestration.plugin_extensions import PluginAgentCatalog
from code_agent.orchestration.supervisor import ChildRunSupervisor
from code_agent.plugins.registry import PluginHost
from code_agent.providers.config import ModelProfile
from code_agent_win.agent_modes import child_mode_for_role
from code_agent_win.child_runner import EngineChildRunner
from code_agent_win.restricted_dispatcher import RestrictedDispatcher

_READ_ONLY_ROLES = {
    AgentRole.ORACLE,
    AgentRole.REVIEW,
    AgentRole.SEARCH,
    AgentRole.LIBRARIAN,
}
_WRITE_TOOLS = frozenset(
    {"write_file", "replace_text", "run_verification", "run_process_v1", "run_command"}
)
_READ_ONLY_TOOLS = frozenset(
    {"read_file", "list_files", "search_text", "git_status", "git_diff", "search_threads", "read_thread"}
)


class SubagentTool:
    def __init__(
        self,
        supervisor: ChildRunSupervisor,
        mode_registry: ModeRegistry,
        profiles: dict[str, ModelProfile],
        agents: PluginAgentCatalog | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._modes = mode_registry
        self._profiles = profiles
        self._agents = agents
        self._parent_run_id = parent_run_id

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        agent, role = self._resolve_agent(request)
        result = await self._supervisor.run(self._child_request(request, agent))
        return _action_result(request, role, result)

    def _resolve_agent(
        self, request: ActionRequest
    ) -> tuple[AgentDefinition, AgentRole]:
        arguments = request.arguments
        role_value = arguments.get("role")
        agent_id = arguments.get("agent_id")
        if (role_value is None) == (agent_id is None):
            raise ValueError("exactly one of role or agent_id is required")
        if agent_id is not None:
            if self._agents is None:
                raise ValueError("plugin agents are unavailable")
            agent = self._agents.resolve(str(agent_id))
            role = agent.role
            snapshot = agent.mode
            allowed = agent.effective_tools
        else:
            role = AgentRole(str(role_value))
            snapshot = self._modes.freeze(
                child_mode_for_role(role), self._profiles
            )
            allowed = snapshot.definition.tool_names
        may_write = (
            agent.may_write
            if agent_id is not None
            else role is AgentRole.SUBAGENT
            and any(name in _WRITE_TOOLS for name in allowed)
        )
        if role in _READ_ONLY_ROLES:
            allowed = tuple(name for name in allowed if name in _READ_ONLY_TOOLS)
        if agent_id is None:
            agent = AgentDefinition(
                f"{role.value}-{request.id[:16]}",
                role,
                snapshot,
                _instructions(role),
                tuple(allowed),
                may_write=may_write,
            )
        return agent, role

    def _child_request(
        self, request: ActionRequest, agent: AgentDefinition
    ) -> ChildRunRequest:
        arguments = request.arguments
        return ChildRunRequest(
            self._parent_run_id or request.id,
            str(arguments["objective"]),
            agent,
            1,
            int(arguments.get("token_budget", 20_000)),
            int(arguments.get("tool_budget", 16)),
            int(arguments.get("active_seconds", 300)),
        )


def _action_result(
    request: ActionRequest, role: AgentRole, result: ChildRunResult
) -> ActionResult:
    output = {
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
    }
    return ActionResult(
        request.id,
        request.name,
        output,
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
        budget: ParentBudget = ParentBudget(),
        *,
        plugin_host: PluginHost | None = None,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot] | None = None,
    ) -> None:
        self._runner = runner
        self._modes = mode_registry
        self._profiles = profiles
        self._budget = budget
        self._parent: ContextVar[str] = ContextVar("subagent_parent", default="adhoc")
        self._supervisors: dict[str, ChildRunSupervisor] = {}
        self._listeners: set[Callable[[RunView], None]] = set()
        self._agents = (
            PluginAgentCatalog(plugin_host, mode_snapshots)
            if plugin_host is not None and mode_snapshots is not None
            else None
        )
        self._child_threads: dict[str, str] = {}
        runner.subscribe_thread(
            lambda run_id, thread_id: self._child_threads.__setitem__(
                run_id, thread_id
            )
        )

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

    def child_thread(self, run_id: str) -> str | None:
        return self._child_threads.get(run_id)

    def activate(self, parent_id: str) -> Token[str]:
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError("parent_id must be non-blank")
        return self._parent.set(parent_id)

    def reset(self, token: Token[str]) -> None:
        self._parent.reset(token)

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        parent_id = self._parent.get()
        supervisor = self._supervisors.get(parent_id)
        if supervisor is None:
            supervisor = ChildRunSupervisor(
                self._runner, BudgetLedger(self._budget), cancellation
            )
            supervisor.subscribe(self._publish)
            self._supervisors[parent_id] = supervisor
        token = self._runner.bind_execution_context(execution_context)
        try:
            return await SubagentTool(
                supervisor,
                self._modes,
                self._profiles,
                self._agents,
                parent_id,
            ).dispatch(request, cancellation)
        finally:
            self._runner.reset_execution_context(token)

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
        self._child_threads.clear()
