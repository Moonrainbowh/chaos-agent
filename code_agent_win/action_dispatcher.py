from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.mcp.registry import McpController
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import DecisionOutcome
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import CommandSpec
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.verification.models import VerificationKind, VerificationRequest, VerificationUnavailable
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitCommandError, GitWorkspace

from code_agent_win.plugin_runtime import PluginToolBridge
from code_agent_win.rewind_capture import is_external_plan, mcp_requires_gap, plugin_requires_gap, record_unknown_gap
from code_agent_win.subagents import SubagentRuntime, SubagentTool
from code_agent_win.tool_support import command_action_result, git_error_result
from code_agent_win.tools import powershell_compatibility_error, tool_definitions, validate_tool_arguments


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
        capture: object | None = None,
        invalidate_cache: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.files, self.editor, self.policy, self.approvals = files, editor, policy, approvals
        self.git, self.runtime, self.verification = git, runtime, verification
        self.mcp, self.plugins, self.subagents = mcp, plugins, subagents
        self.capture = capture
        self.invalidate_cache = invalidate_cache
        self.interactive = False

    def tools(self) -> Sequence[ToolDefinition]:
        builtins = tool_definitions(include_git=self.git is not None)
        mcp = self.mcp.definitions() if self.mcp is not None else ()
        plugins = self.plugins.definitions() if self.plugins is not None else ()
        return builtins + mcp + plugins

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
        rejected = self._preflight(request, translated)
        if rejected is not None:
            return rejected
        rejected = await self._authorize(
            request, translated, cancellation, task_authorization
        )
        if rejected is not None:
            return rejected
        return await self._run(request, translated, cancellation, execution_context)

    def _preflight(self, request: ActionRequest, translated: ActionRequest) -> ActionResult | None:
        validation_error = validate_tool_arguments(translated.name, translated.arguments)
        if validation_error is not None:
            return _error(request, "invalid tool arguments", validation_error)
        if translated.name == "run_command":
            mismatch = powershell_compatibility_error(_text(translated.arguments, "command"))
            if mismatch is not None:
                return _error(request, "shell syntax mismatch", mismatch)
        return None

    async def _authorize(
        self,
        request: ActionRequest,
        translated: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None,
    ) -> ActionResult | None:
        decisions = (self.policy.evaluate(request, task_authorization),)
        if translated is not request:
            decisions += (self.policy.evaluate(translated, task_authorization),)
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
            result = await self._execute(
                translated, cancellation, context, gap_recorded=plugin_gap
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
            return _error(request, "action failed", type(error).__name__)

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
        if request.name.startswith("mcp."):
            if self.mcp is None:
                raise RuntimeError("MCP integration is unavailable")
            if self.capture is not None and not gap_recorded and mcp_requires_gap(
                self.mcp, request.name
            ):
                await record_unknown_gap(self.capture, context, request, cancellation)
            return _ok(request, {"result": await self.mcp.call(request.name, arguments)})
        workspace = await self._execute_workspace(request, cancellation, context)
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
        if request.name == "run_command":
            if self.runtime is None:
                raise RuntimeError("local runtime is unavailable")
            if not gap_recorded:
                await record_unknown_gap(self.capture, context, request, cancellation)
            try:
                result = await self.runtime.run(
                    CommandSpec(cwd=Path("."), powershell_script=_text(arguments, "command")),
                    cancellation,
                    None,
                )
            finally:
                if self.invalidate_cache is not None:
                    self.invalidate_cache(())
            return command_action_result(request, result)
        if request.name == "run_verification":
            return await self._run_verification(
                request, cancellation, context, gap_recorded)
        return _error(request, "tool is not implemented")

    async def _execute_workspace(
        self, request: ActionRequest, cancellation: CancellationToken,
        context: ActionExecutionContext | None,
    ) -> ActionResult | None:
        arguments = request.arguments
        if request.name == "read_file":
            document = await asyncio.to_thread(self.files.read_text, _text(arguments, "path"))
            return _ok(request, {"path": document.relative_path, "text": document.text, "total_lines": document.total_lines})
        if request.name == "list_files":
            root = arguments.get("root")
            if root is not None and not isinstance(root, str):
                raise ValueError("root must be text")
            return _ok(request, {"files": list(await asyncio.to_thread(self.files.list_files, root))})
        if request.name == "search_text":
            matches = await asyncio.to_thread(
                self.files.search,
                _text(arguments, "pattern"),
                bool(arguments.get("regex", False)),
                bool(arguments.get("case_sensitive", False)),
            )
            return _ok(request, {"matches": [match.__dict__ for match in matches]})
        if request.name in {"write_file", "replace_text"}:
            plan = await asyncio.to_thread(self._edit_plan, request)
            if self.capture is not None and not is_external_plan(plan.relative_path):
                if context is None:
                    raise TypeError("execution_context is required for capture")
                cancellation.raise_if_cancelled()
                await self.capture.apply_edit(context, request, plan)
            else:
                await asyncio.to_thread(self.editor.apply, plan)
            if self.invalidate_cache is not None:
                self.invalidate_cache((plan.relative_path,))
            cancellation.raise_if_cancelled()
            return _ok(request, {"path": plan.relative_path}, {"diff": plan.diff})
        return None

    async def _run_verification(
        self, request: ActionRequest, cancellation: CancellationToken,
        context: ActionExecutionContext | None,
        gap_recorded: bool,
    ) -> ActionResult:
        if self.runtime is None or self.verification is None:
            return _error(request, "verification unavailable")
        arguments = request.arguments
        command = self.verification.build(
            VerificationRequest(
                VerificationKind(_text(arguments, "kind")),
                cwd=str(arguments.get("cwd", ".")), targets=tuple(arguments.get("targets", ())),
                timeout_s=int(arguments.get("timeout_s", 300)),
            )
        )
        if isinstance(command, VerificationUnavailable):
            return _error(request, "verification unavailable", command.reason)
        if not gap_recorded:
            await record_unknown_gap(self.capture, context, request, cancellation)
        try:
            result = await self.runtime.run(
                CommandSpec(cwd=Path(command.cwd), argv=command.argv, timeout_s=command.timeout_s),
                cancellation,
                None,
            )
        finally:
            if self.invalidate_cache is not None:
                self.invalidate_cache(())
        action_result = command_action_result(request, result)
        return ActionResult(
            action_result.request_id, action_result.name,
            {**action_result.output, "kind": _text(arguments, "kind")},
            action_result.is_error, action_result.metadata,
        )

    def _edit_plan(self, request: ActionRequest) -> object:
        arguments = request.arguments
        if request.name == "write_file":
            return self.editor.plan_write(_text(arguments, "path"), _text(arguments, "content"))
        return self.editor.plan_replace(
            _text(arguments, "path"), _text(arguments, "old_text"),
            _text(arguments, "new_text"),
        )


def _text(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _ok(request: ActionRequest, output: Mapping[str, object],
        metadata: Mapping[str, str] | None = None) -> ActionResult:
    return ActionResult(request.id, request.name, dict(output), metadata=metadata or {})


def _error(
    request: ActionRequest, message: str, detail: str | None = None, *,
    error_code: str | None = None,
) -> ActionResult:
    output = {"error": message}
    if detail is not None:
        output["detail"] = detail
    if error_code is not None:
        output["error_code"] = error_code
    return ActionResult(request.id, request.name, output, is_error=True)
