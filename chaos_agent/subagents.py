from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from contextvars import ContextVar, Token
from uuid import uuid5, NAMESPACE_URL

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
from chaos_agent.agent_modes import child_mode_for_role
from chaos_agent.child_runner import EngineChildRunner
from chaos_agent.restricted_dispatcher import RestrictedDispatcher

_READ_ONLY_ROLES = {
    AgentRole.ORACLE,
    AgentRole.REVIEW,
    AgentRole.SEARCH,
    AgentRole.LIBRARIAN,
}
_WRITE_TOOLS = frozenset(
    {
        "write_file", "replace_text", "apply_workspace_edit_plan_v1",
        "run_verification", "run_process_v1", "run_command",
    }
)
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file", "read_code_slices", "list_files", "search_text", "git_status", "git_diff",
        "search_threads", "read_thread", "plan_workspace_edits_v1",
    }
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
            run_id=uuid5(NAMESPACE_URL, f"{self._parent_run_id or request.id}:{request.id}").hex,
        )


def distill_subagent_summary(raw_summary: str, max_chars: int = 2_500) -> str:
    """Distill child agent summary to protect parent context budget."""
    if not isinstance(raw_summary, str) or len(raw_summary) <= max_chars:
        return raw_summary
    prefix_len = max_chars // 2
    suffix_len = max_chars // 2
    omitted = len(raw_summary) - (prefix_len + suffix_len)
    return (
        f"{raw_summary[:prefix_len].rstrip()}\n\n"
        f"... [subagent output distilled: {omitted} characters omitted for context economy] ...\n\n"
        f"{raw_summary[-suffix_len:].lstrip()}"
    )


def _action_result(
    request: ActionRequest, role: AgentRole, result: ChildRunResult
) -> ActionResult:
    output = {
        "advisory": True,
        "run_id": result.run_id,
        "role": role.value,
        "status": result.status.value,
        "result": result.result.to_dict() if result.result is not None else None,
        "summary": distill_subagent_summary(result.summary),
        "usage": {
            "tokens": result.usage.total_tokens,
            "tool_calls": result.usage.tool_calls,
            "active_seconds": result.usage.active_seconds,
            "complete": result.usage_complete,
            "known_lower_bound": not result.usage_complete,
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
        budget: ParentBudget | None = None,
        *,
        plugin_host: PluginHost | None = None,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot] | None = None,
        budget_resolver: Callable[[ActionExecutionContext], Awaitable[ParentBudget]] | None = None,
    ) -> None:
        self._runner = runner
        self._modes = mode_registry
        self._profiles = profiles
        self._budget = budget or ParentBudget()
        self._explicit_budget = budget is not None
        self._budget_resolver = budget_resolver
        self._task_ledgers: dict[str, BudgetLedger] = {}
        self._task_owners: dict[str, str] = {}
        self._supervisor_lock = asyncio.Lock()
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
        supervisor = await self._supervisor(parent_id, cancellation, execution_context)
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

    async def _supervisor(
        self, parent_id: str, cancellation: CancellationToken,
        context: ActionExecutionContext | None,
    ) -> ChildRunSupervisor:
        # The lock spans the resolver await: concurrent first calls cannot fork ledgers.
        owned = self._budget_resolver is not None and isinstance(context, ActionExecutionContext) and context.task_id is not None
        if self._budget_resolver is not None and parent_id != "adhoc" and not owned:
            raise ValueError("active parent task requires concrete execution context")
        if owned and context.task_id != parent_id:
            raise ValueError('child execution task does not match the active parent')
        if owned and context.origin_thread_id != context.owner_thread_id:
            raise ValueError("child execution origin does not match the parent owner")
        async with self._supervisor_lock:
            if owned and parent_id in self._task_owners and self._task_owners[parent_id] != context.owner_thread_id:
                raise ValueError("child execution belongs to another parent owner")
            supervisor = self._supervisors.get(parent_id)
            if supervisor is not None:
                return supervisor
            ledger = self._task_ledgers.get(parent_id) if owned else None
            if ledger is None:
                budget = self._budget
                if owned:
                    frozen = await self._budget_resolver(context)
                    budget = replace(budget,
                        max_total_tokens=min(budget.max_total_tokens, frozen.max_total_tokens) if self._explicit_budget else frozen.max_total_tokens,
                        max_tool_calls=min(budget.max_tool_calls, frozen.max_tool_calls) if self._explicit_budget else frozen.max_tool_calls)
                ledger = BudgetLedger(budget)
                if owned:
                    self._task_ledgers[parent_id] = ledger
                    self._task_owners[parent_id] = context.owner_thread_id
            supervisor = ChildRunSupervisor(self._runner, ledger, cancellation)
            supervisor.subscribe(self._publish)
            self._supervisors[parent_id] = supervisor
            return supervisor

    async def release(self, parent_id: str) -> None:
        supervisor = self._supervisors.get(parent_id)
        if supervisor is not None:
            await supervisor.cancel_all("parent execution settling")
            await supervisor.wait_all()
            if self._supervisors.get(parent_id) is supervisor:
                self._supervisors.pop(parent_id)

    async def aclose(self) -> None:
        supervisors = tuple(self._supervisors.values())
        for supervisor in supervisors:
            await supervisor.cancel_all("application closing")
        for supervisor in supervisors:
            await supervisor.wait_all()
        self._supervisors.clear()
        self._task_ledgers.clear()
        self._task_owners.clear()
        self._child_threads.clear()
