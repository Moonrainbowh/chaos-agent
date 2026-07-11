from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional, Sequence

from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.config.loader import load_runtime_config
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode, DecisionOutcome
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.config import (
    ApiProtocol,
    ModelProfile,
    ModelProfileResolver,
    ProviderConfig,
)
from code_agent.providers.openai_chat import OpenAIChatClient
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import CommandSpec
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard


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
    ) -> None:
        self.files = files
        self.editor = editor
        self.policy = policy
        self.approvals = approvals
        self.git = git
        self.runtime = runtime
        self.interactive = False

    def tools(self) -> Sequence[ToolDefinition]:
        return (
            ToolDefinition("read_file", "Read a permitted UTF-8 file.", {"type": "object"}),
            ToolDefinition("list_files", "List permitted files; root enables recursive enumeration.", {"type": "object"}),
            ToolDefinition("search_text", "Search visible workspace text.", {"type": "object"}),
            ToolDefinition("write_file", "Atomically write a reviewed workspace file.", {"type": "object"}),
            ToolDefinition("replace_text", "Replace one exact text occurrence.", {"type": "object"}),
            ToolDefinition("git_status", "Read Git porcelain status.", {"type": "object"}),
            ToolDefinition("git_diff", "Read Git diff for workspace paths.", {"type": "object"}),
            ToolDefinition("run_command", "Run an approved PowerShell command.", {"type": "object"}),
        )

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        decision = self.policy.evaluate(request)
        if decision.outcome is DecisionOutcome.DENY:
            return _error(request, "action denied", decision.reason)
        if decision.outcome is DecisionOutcome.ASK:
            if not self.interactive:
                return _error(request, "approval required in TUI")
            approved = await self.approvals.request(
                ApprovalRequest(request.id, request.name, dict(request.arguments)),
                cancellation,
            )
            if not approved:
                return _error(request, "action denied by user")
        try:
            return await self._execute(request, cancellation)
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
            return _ok(request, {"returncode": result.returncode, "stdout": result.stdout.decode("utf-8", "replace"), "stderr": result.stderr.decode("utf-8", "replace"), "reason": result.reason.value})
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
    tui: WindowsTerminalApp
    dispatcher: RootActionDispatcher
    model: object

    async def aclose(self) -> None:
        close = getattr(self.model, "aclose", None)
        if close is not None:
            await close()


def create_application(
    workspace_root: Path | None = None,
    model_name: str | None = None,
    profile_name: str | None = None,
) -> Application:
    root = (workspace_root or Path.cwd()).resolve()
    runtime_config = load_runtime_config(cli_profile=profile_name)
    guard = WorkspacePathGuard(
        root,
        allow_outside=runtime_config.approval_mode is not ApprovalMode.PLAN,
        allow_sensitive=runtime_config.allow_sensitive_paths,
    )
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    config = ContextConfig(root, root, "You are a careful coding agent.")
    context = WorkspaceContextBuilder(
        config,
        RuleLoader(guard, files, config),
        RepoMapBuilder(files, config),
        DeterministicCompactor(config),
    )
    approvals = ApprovalBroker()
    policy = ActionPolicy(
        PolicyConfig(runtime_config.approval_mode, workspace_root=root)
    )
    dispatcher = RootActionDispatcher(
        files,
        WorkspaceEditor(guard),
        policy,
        approvals,
        git=GitWorkspace(root),
        runtime=WindowsLocalRuntime(root),
    )
    sessions = SQLiteSessionRepository(_session_path())
    model, profile = _model_client(model_name, runtime_config.provider)
    limits = EngineLimits(
        max_agent_rounds=profile.max_agent_rounds,
        max_tool_calls=profile.max_tool_calls,
        max_tool_calls_per_round=profile.max_tool_calls_per_round,
    )
    controller = AgentController(
        AgentEngine(model, context, dispatcher, sessions, limits=limits, model_name=profile.name)
    )
    return Application(
        controller,
        WindowsTerminalApp(controller, approvals, sessions=sessions),
        dispatcher,
        model,
    )


def _model_client(
    model_name: str | None = None, provider: ProviderConfig | None = None
) -> tuple[object, ModelProfile]:
    profile = _model_profiles(provider).select(model_name)
    config = replace(
        profile.provider, max_tool_calls=profile.max_tool_calls_per_round
    )
    if config.api is ApiProtocol.RESPONSES:
        return OpenAIResponsesClient(config), profile
    if config.api is ApiProtocol.CHAT_COMPLETIONS:
        return OpenAIChatClient(config), profile
    return AnthropicClient(config), profile


def _model_profiles(provider: ProviderConfig | None = None) -> ModelProfileResolver:
    raw = os.getenv("CODE_AGENT_MODEL_PROFILES")
    default_name = os.getenv("CODE_AGENT_DEFAULT_MODEL")
    if raw is None:
        name = default_name or os.getenv("CODE_AGENT_MODEL", "gpt-4.1-mini")
        profile = ModelProfile(
            name=name,
            provider=provider or _provider_config({"model": name}),
            context_window=128_000,
            max_output_tokens=16_384,
        )
        return ModelProfileResolver({name: profile}, name)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("CODE_AGENT_MODEL_PROFILES must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("CODE_AGENT_MODEL_PROFILES must be an object")
    profiles: dict[str, ModelProfile] = {}
    for name, values in payload.items():
        if not isinstance(name, str) or not isinstance(values, dict):
            raise ValueError("model profiles must map names to objects")
        profiles[name] = ModelProfile(
            name=name,
            provider=_provider_config(values),
            context_window=_positive(values, "context_window"),
            max_output_tokens=_positive(values, "max_output_tokens"),
            max_agent_rounds=_positive(values, "max_agent_rounds", 50),
            max_tool_calls=_positive(values, "max_tool_calls", 128),
            max_tool_calls_per_round=_positive(values, "max_tool_calls_per_round", 50),
        )
    return ModelProfileResolver(profiles, default_name or next(iter(profiles), ""))


def _provider_config(values: Mapping[str, object]) -> ProviderConfig:
    protocol = ApiProtocol(values.get("api", os.getenv("CODE_AGENT_API", "responses")))
    return ProviderConfig(
        base_url=_string(
            values, "base_url", os.getenv("CODE_AGENT_BASE_URL", "https://api.openai.com")
        ),
        model=_string(values, "model"),
        api=protocol,
        api_key_env=_string(values, "api_key_env", os.getenv("CODE_AGENT_API_KEY_ENV", "OPENAI_API_KEY")),
    )


def _positive(values: Mapping[str, object], name: str, default: int | None = None) -> int:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"model profile {name} must be a positive integer")
    return value


def _string(values: Mapping[str, object], name: str, default: str | None = None) -> str:
    value = values.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model profile {name} must be non-blank text")
    return value


def _session_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "code-agent"
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


def _error(request: ActionRequest, message: str, detail: str | None = None) -> ActionResult:
    output = {"error": message}
    if detail is not None:
        output["detail"] = detail
    return ActionResult(request.id, request.name, output, is_error=True)
