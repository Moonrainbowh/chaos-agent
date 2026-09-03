from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.context.budget import PromptBudget
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.context_request import ContextRequest
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.orchestration.models import ModeSnapshot
from code_agent.providers.config import ModelProfile
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from code_agent.verification.task_service import (
    LedgerTaskVerificationService,
)
from code_agent_win.agent_modes import mode_prompt
from code_agent_win.peer_context import PEER_CONTEXT_RESERVE_TOKENS
from code_agent_win.context_runtime import build_context_runtime
from code_agent_win.runtime_extensions import (
    BoundSkillContextBuilder,
    ModelSemanticSummarizer,
)
from code_agent_win.tool_support import windows_system_prompt


class RuntimeContextFactory:
    def __init__(
        self,
        root: Path,
        *,
        git_available: bool,
        repo_map_enabled: bool,
        guard: object,
        files: object,
        repo_index: object,
        repo_view_cache: object,
        sessions: object,
        thread_binding: object,
        skills: object,
        workspace_runtime: object | None = None,
        powershell: PowerShellRuntimeResolver | None = None,
        context_runtime_factory: object = build_context_runtime,
    ) -> None:
        self._root = root
        self._git_available = git_available
        self._repo_map_enabled = repo_map_enabled
        self._guard, self._files = guard, files
        self._repo_index, self._repo_view_cache = (
            repo_index,
            repo_view_cache,
        )
        self._sessions = sessions
        self._thread_binding, self._skills = thread_binding, skills
        self._workspace_runtime = workspace_runtime
        self._powershell = powershell or PowerShellRuntimeResolver()
        self._context_runtime_factory = context_runtime_factory

    def __call__(
        self,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
    ) -> object:
        if self._workspace_runtime is not None:
            return _ThreadRootContextBuilder(self, mode, client, profile)
        return self._build(mode, client, profile, self._root)

    def _build(
        self,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
        root: Path,
    ) -> object:
        guard, files, repo_index = self._workspace_parts(root)
        prompt = (
            windows_system_prompt(
                self._git_available, self._powershell.resolve()
            )
            + "\n\n"
            + mode_prompt(mode)
        )
        config = ContextConfig(
            root,
            root,
            prompt,
            prompt_budget=_profile_prompt_budget(profile),
            repo_map_enabled=self._repo_map_enabled,
        )
        rules = RuleLoader(guard, files, config)
        repo_map = RepoMapBuilder(
            files,
            config,
            index=repo_index,
            view_cache=self._repo_view_cache,
        )
        context_limit, target_tokens, summary_tokens, model_token_budget = (
            _semantic_limits(config, profile)
        )
        semantic = self._context_runtime_factory(
            config,
            rules,
            repo_map,
            self._skills,
            self._sessions,
            summarizer=ModelSemanticSummarizer(
                client, profile.provider.model, self._sessions
            ),
            context_limit=context_limit,
            target_tokens=target_tokens,
            summary_tokens=summary_tokens,
            model_token_budget=model_token_budget,
        )
        return BoundSkillContextBuilder(
            semantic, self._thread_binding, self._skills
        )

    def _workspace_parts(self, root: Path) -> tuple[object, object, object]:
        if root == self._root or self._workspace_runtime is None:
            return self._guard, self._files, self._repo_index
        services = self._workspace_runtime.services_for_root(root)
        return services.guard, services.files, services.repo_index


def _profile_prompt_budget(profile: ModelProfile) -> PromptBudget:
    base = PromptBudget()
    prompt_tokens = min(base.max_prompt_tokens, profile.context_window)
    safety_tokens = min(
        max(base.safety_tokens, PEER_CONTEXT_RESERVE_TOKENS),
        max(0, prompt_tokens - 1),
    )
    message_room = max(1, prompt_tokens - safety_tokens)
    max_messages = min(base.max_message_tokens, message_room)
    return replace(
        base,
        max_prompt_tokens=prompt_tokens,
        max_message_tokens=max_messages,
        min_message_tokens=min(base.min_message_tokens, max_messages),
        safety_tokens=safety_tokens,
    )


def _semantic_limits(
    config: ContextConfig, profile: ModelProfile
) -> tuple[int, int, int, int]:
    context_limit = min(
        config.prompt_budget.max_message_tokens,
        profile.context_window,
    )
    target_tokens = min(
        context_limit,
        max(1, profile.context_window - profile.max_output_tokens),
        max(1, config.prompt_budget.max_message_tokens * 3 // 4),
    )
    summary_tokens = min(
        1_024, profile.max_output_tokens, profile.context_window
    )
    model_token_budget = min(8_192, profile.context_window)
    return context_limit, target_tokens, summary_tokens, model_token_budget


class _ThreadRootContextBuilder:
    def __init__(
        self,
        factory: RuntimeContextFactory,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
    ) -> None:
        self._factory = factory
        self._mode, self._client, self._profile = mode, client, profile
        default = factory._build(mode, client, profile, factory._root)
        self._builders = {factory._root: default}
        self._inner = default._inner

    async def build(
        self,
        thread_id: str | ContextRequest,
        messages: object = None,
        user_input: str | None = None,
        tools: object = None,
        task_state: object = None,
        cancellation: object = None,
    ) -> object:
        if isinstance(thread_id, ContextRequest):
            request = thread_id
            active_thread_id = request.thread_id
        else:
            request = None
            active_thread_id = thread_id
        root = self._factory._workspace_runtime.root_for_thread(active_thread_id)
        if root is None:
            await self._factory._workspace_runtime.hydrate_bindings()
            root = self._factory._workspace_runtime.root_for_thread(active_thread_id)
        active_root = root or self._factory._root
        builder = self._builders.get(active_root)
        if builder is None:
            builder = self._factory._build(
                self._mode, self._client, self._profile, active_root
            )
            self._builders[active_root] = builder
        if request is not None:
            return await builder.build(request)
        return await builder.build(
            active_thread_id, messages, user_input, tools, task_state, cancellation
        )


def engine_for(
    model: object,
    profile: ModelProfile,
    context: object,
    dispatcher: object,
    sessions: object,
    workspace_root: Path,
    mode: ModeSnapshot,
    *,
    capability_strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
) -> AgentEngine:
    mode_limits = mode.definition.limits
    limits = EngineLimits(
        min(profile.max_agent_rounds, mode_limits.max_agent_rounds),
        min(profile.max_tool_calls, mode_limits.max_tool_calls),
        min(
            profile.max_tool_calls_per_round,
            mode_limits.max_tool_calls_per_round,
        ),
        min(
            profile.context_window + profile.max_output_tokens,
            mode_limits.max_total_tokens,
        ),
        mode_limits.max_assistant_chars,
    )
    return AgentEngine(
        model,
        context,
        dispatcher,
        sessions,
        limits=limits,
        model_name=profile.provider.model,
        verification=TaskScopedVerificationService(sessions),
        peer_tool_names=("list_agents", "send_message"),
        capability_strategy=capability_strategy,
    )


class TaskScopedVerificationService:
    def __init__(self, sessions: object) -> None:
        self._sessions = sessions

    async def prepare(self, task: object, state: object) -> object:
        return await self._service(task).prepare(task, state)

    async def record_action(
        self, task: object, request: object, result: object, state: object
    ) -> object:
        return await self._service(task).record_action(
            task, request, result, state
        )

    async def assess(self, task: object, state: object) -> object:
        return await self._service(task).assess(task, state)

    async def suggest_verification(self, task: object, state: object) -> object:
        return await self._service(task).suggest_verification(task, state)

    async def finalize(self, task: object, assessment: object) -> object:
        return await self._service(task).finalize(task, assessment)

    def _service(self, task: object) -> LedgerTaskVerificationService:
        root = Path(task.contract.authorization.workspace_root)
        return LedgerTaskVerificationService(root, self._sessions)
