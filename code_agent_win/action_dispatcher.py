from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.mcp.registry import McpController
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import DecisionOutcome
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitCommandError, GitWorkspace

from code_agent_win.edit_plan_dispatch import (
    EditPlanAuthorization,
    WorkspaceEditPlanActions,
)
from code_agent_win.edit_plan_preview import default_workspace_fingerprint
from code_agent_win.edit_plan_store import EditPlanStoreError, WorkspaceEditPlanStore
from code_agent_win.plugin_runtime import PluginToolBridge
from code_agent_win.process_actions import run_powershell_action, run_process_action
from code_agent_win.rewind_capture_support import (
    mcp_requires_gap,
    plugin_requires_gap,
    record_unknown_gap,
)
from code_agent_win.subagents import SubagentRuntime, SubagentTool
from code_agent_win.tool_support import git_error_result
from code_agent_win.tools import tool_definitions
from code_agent.thread_intelligence.tools import ThreadIntelligenceTools
from code_agent_win.action_support import (
    error_result as _error,
    exception_result as _exception,
    ok_result as _ok,
    preflight_action,
    with_action_duration,
)
from code_agent_win.thread_actions import execute_thread_action
from code_agent_win.verification_action import run_verification_action
from code_agent_win.workspace_actions import execute_workspace_action


class RootActionDispatcher:
    """Integration-only typed action router guarded by the central policy."""

    def __init__(
        self,
        files: WorkspaceFiles,
        editor: WorkspaceEditor,
        policy: ActionPolicy,
        approvals: ApprovalBroker,
        *,
        git: GitWorkspace | None = None,
        runtime: WindowsLocalRuntime | None = None,
        verification: LocalVerificationAdapter | None = None,
        mcp: McpController | None = None,
        plugins: PluginToolBridge | None = None,
        subagents: SubagentTool | None = None,
        threads: ThreadIntelligenceTools | None = None,
        peers: object | None = None,
        capture: object | None = None,
        caller_thread: Callable[[], str] | None = None,
        invalidate_cache: Callable[[Sequence[str]], None] | None = None,
        edit_plans: WorkspaceEditPlanStore | None = None,
        workspace_fingerprint: str | None = None,
        repo_index: object | None = None,
    ) -> None:
        self.files, self.editor, self.policy, self.approvals = files, editor, policy, approvals
        self.git, self.runtime, self.verification = git, runtime, verification
        self.mcp, self.plugins, self.subagents = mcp, plugins, subagents
        self.threads, self.peers, self.caller_thread = threads, peers, caller_thread
        self.capture = capture
        self.repo_index = repo_index
        self.invalidate_cache = invalidate_cache
        self.edit_plans = edit_plans or WorkspaceEditPlanStore()
        captured = getattr(capture, "workspace_fingerprint", None)
        fingerprint = workspace_fingerprint or (
            captured if isinstance(captured, str) else default_workspace_fingerprint(editor)
        )
        self.edit_plan_actions = WorkspaceEditPlanActions(
            editor,
            self.edit_plans,
            fingerprint,
            git=git,
            capture=capture,
            invalidate_cache=invalidate_cache,
        )
        self.interactive = False

    def tools(self) -> Sequence[ToolDefinition]:
        powershell = self.runtime.powershell_info() if isinstance(self.runtime, WindowsLocalRuntime) else None
        builtins = tool_definitions(
            include_git=self.git is not None, powershell=powershell
        )
        mcp = self.mcp.definitions() if self.mcp is not None else ()
        plugins = self.plugins.definitions() if self.plugins is not None else ()
        threads = self.threads.definitions() if self.threads is not None else ()
        peer_tools = (
            tuple(
                tool
                for tool in self.peers.tools()
                if tool.name in {"list_agents", "send_message"}
            )
            if self.peers is not None
            else ()
        )
        return builtins + threads + peer_tools + mcp + plugins

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        target = self.plugins.targets().get(request.name) if self.plugins else None
        translated = ActionRequest(request.id, target, request.arguments) if target else request
        rejected = preflight_action(request, translated)
        if rejected is not None:
            return rejected
        try:
            edit_authorization = self.edit_plan_actions.authorization(
                translated, execution_context
            )
        except EditPlanStoreError as error:
            return _error(
                request,
                "edit plan is not applicable",
                str(error),
                error_code=error.code,
            )
        except (TypeError, ValueError) as error:
            return _error(request, "invalid edit plan context", str(error))
        rejected = await self._authorize(
            request,
            translated,
            cancellation,
            task_authorization,
            edit_authorization,
        )
        if rejected is not None:
            return rejected
        return await self._run(request, translated, cancellation, execution_context)

    async def _authorize(
        self,
        request: ActionRequest,
        translated: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None,
        edit_authorization: EditPlanAuthorization,
    ) -> ActionResult | None:
        def evaluate(item: ActionRequest):
            risks = (
                edit_authorization.risk_flags
                if item.name == "apply_workspace_edit_plan_v1"
                else ()
            )
            if risks:
                return self.policy.evaluate(
                    item,
                    task_authorization,
                    trusted_edit_risk_flags=risks,
                )
            return self.policy.evaluate(item, task_authorization)

        decisions = (evaluate(request),)
        if translated is not request:
            decisions += (evaluate(translated),)
        denied = next((item for item in decisions if item.outcome is DecisionOutcome.DENY), None)
        if denied is not None:
            return _error(request, "action denied", denied.reason)
        asking = next((item for item in decisions if item.outcome is DecisionOutcome.ASK), None)
        if asking is not None:
            if not self.interactive:
                return _error(request, "approval required in TUI", error_code="approval_required")
            approved = await self.approvals.request(
                ApprovalRequest(
                    request.id, request.name, dict(request.arguments),
                    asking.risk.value, translated.name, asking.reason,
                    edit_authorization.view,
                ),
                cancellation,
            )
            if not approved:
                return _error(request, "action denied by user")
        return None

    async def _run(
        self,
        request: ActionRequest,
        translated: ActionRequest,
        cancellation: CancellationToken,
        context: ActionExecutionContext | None,
    ) -> ActionResult:
        try:
            plugin_gap = (
                self.capture is not None
                and plugin_requires_gap(self.plugins, request, translated)
            )
            if plugin_gap:
                await record_unknown_gap(self.capture, context, translated, cancellation)
            result = await with_action_duration(
                self._execute(
                    translated, cancellation, context, gap_recorded=plugin_gap
                )
            )
            if translated is request:
                return result
            return ActionResult(
                request.id, request.name, result.output, result.is_error,
                {**result.metadata, "plugin_target": translated.name},
            )
        except GitCommandError as error:
            return git_error_result(request, error)
        except (CancellationError, asyncio.CancelledError):
            raise
        except Exception as error:
            return _exception(request, error)

    async def _execute(
        self, request: ActionRequest, cancellation: CancellationToken,
        context: ActionExecutionContext | None,
        *,
        gap_recorded: bool,
    ) -> ActionResult:
        arguments = request.arguments
        if request.name == "delegate_agent":
            if self.subagents is None:
                return _error(request, "subagent runtime unavailable")
            if isinstance(self.subagents, SubagentRuntime):
                return await self.subagents.dispatch(request, cancellation, execution_context=context)
            return await self.subagents.dispatch(request, cancellation)
        if request.name in {"search_threads", "read_thread"}:
            if self.threads is None or self.caller_thread is None:
                return _error(request, "thread intelligence unavailable")
            return await execute_thread_action(
                request, self.threads, self.caller_thread
            )
        if request.name in {"list_agents", "send_message"}:
            if self.peers is None:
                return _error(request, "peer messaging unavailable")
            return await self.peers.dispatch(
                request, cancellation, execution_context=context
            )
        if request.name.startswith("mcp."):
            if self.mcp is None:
                raise RuntimeError("MCP integration is unavailable")
            if self.capture is not None and not gap_recorded and mcp_requires_gap(
                self.mcp, request.name
            ):
                await record_unknown_gap(self.capture, context, request, cancellation)
            return _ok(request, {"result": await self.mcp.call(request.name, arguments)})
        workspace = await execute_workspace_action(
            self, request, cancellation, context
        )
        if workspace is not None:
            return workspace
        if request.name == "git_status":
            if self.git is None:
                raise RuntimeError("git integration is unavailable")
            return _ok(request, {"status": await asyncio.to_thread(self.git.status_porcelain)})
        if request.name == "git_diff":
            if self.git is None:
                raise RuntimeError("git integration is unavailable")
            paths = arguments.get("paths", ())
            if not isinstance(paths, (list, tuple)) or not all(isinstance(path, str) for path in paths):
                raise ValueError("paths must be a list of strings")
            return _ok(request, {"diff": await asyncio.to_thread(self.git.diff, paths)})
        if request.name in {"run_command", "run_process_v1"}:
            if self.runtime is None:
                raise RuntimeError("local runtime is unavailable")
            if not gap_recorded:
                await record_unknown_gap(self.capture, context, request, cancellation)
            action = (
                run_powershell_action
                if request.name == "run_command"
                else run_process_action
            )
            return await action(request, self.runtime, cancellation, self.invalidate_cache)
        if request.name == "run_verification":
            return await run_verification_action(
                request,
                self.runtime,
                self.verification,
                self.capture,
                context,
                cancellation,
                gap_recorded,
                self.invalidate_cache,
            )
        return _error(request, "tool is not implemented")
