from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Mapping, Optional, Sequence

from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.config.loader import load_runtime_config
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.cache import RepoMapCache
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.interfaces.profile_control import ProfileControl
from code_agent.skills.registry import SkillActivation, SkillContextBuilder, SkillRegistry
from code_agent.mcp.registry import McpRegistry
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode, DecisionOutcome
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.providers.openai_chat import OpenAIChatClient
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import CommandSpec
from code_agent.verification.models import VerificationKind, VerificationRequest, VerificationUnavailable
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitCommandError, GitWorkspace
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.tool_support import (
    command_action_result,
    discover_git_workspace,
    git_error_result,
    windows_system_prompt,
)
from code_agent_win.tools import (
    powershell_compatibility_error,
    tool_definitions,
    validate_tool_arguments,
)


class RootActionDispatcher:
    """Integration-only typed action router guarded by the central policy."""

    def __init__(
        self,
        files: WorkspaceFiles,
        editor: WorkspaceEditor,
        policy: ActionPolicy,
        approvals: ApprovalBroker,
        *,
        git: Optional[GitWorkspace] = None,
        runtime: Optional[WindowsLocalRuntime] = None,
        verification: LocalVerificationAdapter | None = None,
        invalidate_cache: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.files = files
        self.editor = editor
        self.policy = policy
        self.approvals = approvals
        self.git = git
        self.runtime = runtime
        self.verification = verification
        self.invalidate_cache = invalidate_cache
        self.interactive = False

    def tools(self) -> Sequence[ToolDefinition]:
        return tool_definitions(include_git=self.git is not None)

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
    ) -> ActionResult:
        validation_error = validate_tool_arguments(request.name, request.arguments)
        if validation_error is not None:
            return _error(request, "invalid tool arguments", validation_error)
        if request.name == "run_command":
            compatibility_error = powershell_compatibility_error(
                _text(request.arguments, "command")
            )
            if compatibility_error is not None:
                return _error(
                    request, "shell syntax mismatch", compatibility_error
                )
        decision = self.policy.evaluate(request, task_authorization)
        if decision.outcome is DecisionOutcome.DENY:
            return _error(request, "action denied", decision.reason)
        if decision.outcome is DecisionOutcome.ASK:
            if not self.interactive:
                return _error(request, "approval required in TUI", error_code="approval_required")
            approved = await self.approvals.request(
                ApprovalRequest(request.id, request.name, dict(request.arguments)),
                cancellation,
            )
            if not approved:
                return _error(request, "action denied by user")
        try:
            return await self._execute(request, cancellation)
        except GitCommandError as error:
            return git_error_result(request, error)
        except Exception as error:
            return _error(request, "action failed", type(error).__name__)

    async def _execute(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        arguments = request.arguments
        if request.name == "read_file":
            document = await asyncio.to_thread(
                self.files.read_text, _text(arguments, "path")
            )
            return _ok(request, {"path": document.relative_path, "text": document.text, "total_lines": document.total_lines})
        if request.name == "list_files":
            root = arguments.get("root")
            if root is not None and not isinstance(root, str):
                raise ValueError("root must be text")
            files = await asyncio.to_thread(self.files.list_files, root)
            return _ok(request, {"files": list(files)})
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
            await asyncio.to_thread(self.editor.apply, plan)
            if self.invalidate_cache is not None:
                self.invalidate_cache((plan.relative_path,))
            return _ok(request, {"path": plan.relative_path}, {"diff": plan.diff})
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
            result = await self.runtime.run(
                CommandSpec(cwd=Path("."), powershell_script=_text(arguments, "command")),
                cancellation,
                None,
            )
            return command_action_result(request, result)
        if request.name == "run_verification":
            if self.runtime is None or self.verification is None:
                return _error(request, "verification unavailable")
            command = self.verification.build(
                VerificationRequest(
                    VerificationKind(_text(arguments, "kind")),
                    cwd=str(arguments.get("cwd", ".")),
                    targets=tuple(arguments.get("targets", ())),
                    timeout_s=int(arguments.get("timeout_s", 300)),
                )
            )
            if isinstance(command, VerificationUnavailable):
                return _error(request, "verification unavailable", command.reason)
            result = await self.runtime.run(
                CommandSpec(cwd=Path(command.cwd), argv=command.argv, timeout_s=command.timeout_s),
                cancellation,
                None,
            )
            action_result = command_action_result(request, result)
            return ActionResult(
                action_result.request_id,
                action_result.name,
                {**action_result.output, "kind": _text(arguments, "kind")},
                action_result.is_error,
                action_result.metadata,
            )
        return _error(request, "tool is not implemented")

    def _edit_plan(self, request: ActionRequest):
        arguments = request.arguments
        if request.name == "write_file":
            return self.editor.plan_write(_text(arguments, "path"), _text(arguments, "content"))
        return self.editor.plan_replace(
            _text(arguments, "path"),
            _text(arguments, "old_text"),
            _text(arguments, "new_text"),
        )


@dataclass
class Application:
    controller: AgentController
    foreground_tasks: ForegroundTaskController
    tui: WindowsTerminalApp
    dispatcher: RootActionDispatcher
    model: object

    async def aclose(self) -> None:
        close = getattr(self.model, "aclose", None)
        if close is not None:
            await close()


def create_application(
    workspace_root: Path | None = None,
    *,
    model_name: str | None = None,
    profile_name: str | None = None,
) -> Application:
    if profile_name is not None and (not isinstance(profile_name, str) or not profile_name.strip()):
        raise ValueError("profile_name must be non-blank text")
    root = (workspace_root or Path.cwd()).resolve()
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    config = ContextConfig(root, root, windows_system_prompt(git is not None))
    cache = RepoMapCache(root)
    context = WorkspaceContextBuilder(
        config,
        RuleLoader(guard, files, config),
        RepoMapBuilder(files, config, cache=cache),
        DeterministicCompactor(config),
    )
    approvals = ApprovalBroker()
    runtime_config = load_runtime_config(cli_profile=profile_name)
    mode = runtime_config.approval_mode
    policy = ActionPolicy(PolicyConfig(mode, workspace_root=root))
    dispatcher = RootActionDispatcher(
        files,
        WorkspaceEditor(guard),
        policy,
        approvals,
        git=git,
        runtime=WindowsLocalRuntime(root),
        verification=LocalVerificationAdapter(root),
        invalidate_cache=cache.invalidate,
    )
    sessions = SQLiteSessionRepository(_session_path())
    initial = next(profile for profile in runtime_config.profiles if profile.name == runtime_config.profile)
    if model_name is not None:
        initial = ModelProfile(initial.name, _provider_with_model(initial.provider, model_name), initial.context_window, initial.max_output_tokens, initial.max_agent_rounds, initial.max_tool_calls, initial.max_tool_calls_per_round)
    skills = SkillActivation(SkillRegistry.discover(root))
    skill_context = SkillContextBuilder(context, skills)
    model = _model_client(initial.provider)
    controller = AgentController(_engine_for(model, initial, skill_context, dispatcher, sessions, root))
    def apply_profile(profile: ModelProfile) -> None:
        controller.replace_runner(_engine_for(_model_client(profile.provider), profile, skill_context, dispatcher, sessions, root))
    profiles = ProfileControl({profile.name: profile for profile in runtime_config.profiles}, runtime_config.profile, apply_profile)
    foreground_tasks = ForegroundTaskController(controller, sessions, root)
    return Application(
        controller,
        foreground_tasks,
        WindowsTerminalApp(controller, approvals, sessions=sessions, evidence=sessions, history=sessions, tasks=foreground_tasks, profiles=profiles, skills=skills, mcp=McpRegistry(runtime_config.mcp_servers)),
        dispatcher,
        model,
    )


def _model_client(config: ProviderConfig) -> object:
    protocol = config.api
    if protocol is ApiProtocol.RESPONSES:
        return OpenAIResponsesClient(config)
    if protocol is ApiProtocol.CHAT_COMPLETIONS:
        return OpenAIChatClient(config)
    return AnthropicClient(config)


def _engine_for(model: object, profile: ModelProfile, context: object, dispatcher: object, sessions: object, workspace_root: Path) -> AgentEngine:
    limits = EngineLimits(profile.max_agent_rounds, profile.max_tool_calls, profile.max_tool_calls_per_round, profile.context_window + profile.max_output_tokens)
    return AgentEngine(
        model,
        context,
        dispatcher,
        sessions,
        limits=limits,
        model_name=profile.provider.model,
        verification=LedgerTaskVerificationService(workspace_root, sessions),
    )


def _provider_with_model(config: ProviderConfig, model: str) -> ProviderConfig:
    return ProviderConfig(config.base_url, model, config.api, config.api_key_env, config.api_key_source, config.timeout_s, config.max_retries, config.max_event_bytes, config.max_response_bytes, config.max_tool_argument_bytes, config.max_tool_calls, config.responses_path, config.chat_completions_path, config.anthropic_messages_path)


def _session_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "chaos-agent"
    legacy = Path(base) / "code-agent" / "sessions.sqlite3"
    if not directory.exists() and legacy.exists():
        return legacy
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "sessions.sqlite3"


def _text(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _ok(
    request: ActionRequest,
    output: Mapping[str, object],
    metadata: Optional[Mapping[str, str]] = None,
) -> ActionResult:
    return ActionResult(request.id, request.name, dict(output), metadata=metadata or {})


def _error(request: ActionRequest, message: str, detail: str | None = None, *, error_code: str | None = None) -> ActionResult:
    output = {"error": message}
    if detail is not None:
        output["detail"] = detail
    if error_code is not None:
        output["error_code"] = error_code
    return ActionResult(request.id, request.name, output, is_error=True)
