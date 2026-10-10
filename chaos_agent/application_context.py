from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.context.budget import PromptBudget
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.context_request import ContextRequest
from code_agent.core.engine import AgentEngine
from code_agent.core.action_execution import ActionLineage
from code_agent.core.task import TaskAuthorization
from code_agent.core.limits import EngineLimits
from code_agent.orchestration.models import ModeSnapshot
from code_agent.providers.config import ModelProfile
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from chaos_agent.agent_modes import mode_prompt
from chaos_agent.peer_context import PEER_CONTEXT_RESERVE_TOKENS
from chaos_agent.context_runtime import build_context_runtime
from chaos_agent.runtime_extensions import (
    BoundSkillContextBuilder,
    ModelSemanticSummarizer,
)
from chaos_agent.tool_support import windows_system_prompt
from chaos_agent.task_verification import TaskScopedVerificationService
from chaos_agent.verification_mode import structured_verification_enabled
from chaos_agent.managed_context import build_managed_context, configured_counter
from chaos_agent.context_assembly import ContextAssembly, ContextScopedDispatcher
from chaos_agent.project_memory_context import ProjectMemoryContextBuilder
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy, RequestBudgetConstraints


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
        project_memory: object | None = None,
        parent_review_model_factory: object | None = None,
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
        self._project_memory = project_memory
        self._parent_review_model_factory = parent_review_model_factory

    def __call__(
        self,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
    ) -> object:
        guarded = self._budgeted_client(mode, client, profile)
        if self._workspace_runtime is not None:
            builder = _ThreadRootContextBuilder(self, mode, guarded, profile)
            result = ContextAssembly(builder, guarded, builder.context_actions,
                                     builder.semantic_snapshot_for_root)
        else:
            result = self._build(mode, guarded, profile, self._root)
        review = (self._parent_review_model_factory(guarded, profile, mode)
                  if self._parent_review_model_factory is not None else None)
        return replace(result, compact_binding=self._thread_binding, parent_review_model=review)

    def _budgeted_client(self, mode, client, profile):
        prompt_budget = _profile_prompt_budget(profile, mode)
        constraints = RequestBudgetConstraints(
            host_prompt_tokens=prompt_budget.max_prompt_tokens)
        if isinstance(client, BudgetedWindowClient):
            existing = client.constraints.host_prompt_tokens
            if existing is None or existing > constraints.host_prompt_tokens:
                raise ValueError("existing request guard lacks the frozen Host ceiling")
            return client
        policy = profile.context_policy or WindowPolicy(
            work_tokens=profile.context_window,
            safety_tokens=_profile_prompt_budget(profile, mode).safety_tokens,
            task_tokens=mode.definition.limits.max_total_tokens)
        return BudgetedWindowClient(client, self._sessions, self._thread_binding.current,
            policy, ApiContextLimits(profile.context_window, profile.max_output_tokens,
                profile.api_input_tokens), configured_counter(profile.provider.model),
            constraints=constraints)

    def for_child(self, mode, client, profile, root, sessions, *, agent_instructions=""):
        """Freeze child reads to the same authorized root as child actions."""
        factory = RuntimeContextFactory(root,
            git_available=self._git_available, repo_map_enabled=self._repo_map_enabled,
            guard=self._guard, files=self._files, repo_index=self._repo_index,
            repo_view_cache=self._repo_view_cache, sessions=sessions,
            thread_binding=self._thread_binding, skills=self._skills,
            workspace_runtime=self._workspace_runtime, powershell=self._powershell,
            context_runtime_factory=self._context_runtime_factory,
            project_memory=self._project_memory)
        # Resolve services on the original factory, whose source root is authoritative.
        factory._guard, factory._files, factory._repo_index = self._workspace_parts(root)
        guarded = factory._budgeted_client(mode, client, profile)
        result = factory._build(mode, guarded, profile, root, agent_instructions=agent_instructions)
        return replace(result, compact_binding=self._thread_binding)

    def _build(
        self,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
        root: Path,
        *,
        agent_instructions: str = "",
    ) -> object:
        guard, files, repo_index = self._workspace_parts(root)
        powershell = self._powershell.resolve() if os.name == "nt" else None
        prompt = (
            windows_system_prompt(
                self._git_available, powershell
            )
            + "\n\n"
            + mode_prompt(mode)
        )
        config = ContextConfig(
            root,
            root,
            prompt,
            prompt_budget=_profile_prompt_budget(profile, mode),
            repo_map_enabled=self._repo_map_enabled,
            agent_instructions=agent_instructions,
        )
        rules = RuleLoader(guard, files, config)
        repo_map = RepoMapBuilder(
            files,
            config,
            index=repo_index,
            view_cache=self._repo_view_cache,
        )
        assembly = self._strategy_context(config, rules, repo_map, client, profile)
        if self._project_memory is not None:
            assembly = assembly.map_builder(
                lambda builder: ProjectMemoryContextBuilder(builder, self._project_memory))
        return assembly

    def _strategy_context(self, config, rules, repo_map, client, profile):
        """Select one complete history strategy for this frozen model profile."""
        if profile.context_policy is not None:
            return build_managed_context(config, rules, repo_map, self._skills,
                self._sessions, self._thread_binding, client, profile)
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
            return_assembly=True,
        )
        if not isinstance(semantic, ContextAssembly):
            raise TypeError("production context factory must return ContextAssembly")
        snapshot, builder = semantic.semantic_snapshot, semantic.builder
        return ContextAssembly(BoundSkillContextBuilder(
            builder, self._thread_binding, self._skills, semantic_snapshot=snapshot
        ), client, semantic_snapshot=snapshot)

    def _workspace_parts(self, root: Path) -> tuple[object, object, object]:
        if root == self._root or self._workspace_runtime is None:
            return self._guard, self._files, self._repo_index
        services = self._workspace_runtime.services_for_root(root)
        return services.guard, services.files, services.repo_index


def _profile_prompt_budget(
    profile: ModelProfile, mode: ModeSnapshot | None = None
) -> PromptBudget:
    is_ultra = False
    if mode is not None and hasattr(mode, "definition"):
        mode_val = getattr(mode.definition.mode, "value", str(mode.definition.mode))
        if mode_val == "ultra":
            is_ultra = True

    env_max_prompt = os.getenv("CHAOS_MAX_PROMPT_TOKENS")
    if env_max_prompt and env_max_prompt.isdigit():
        custom_max = int(env_max_prompt)
        base = PromptBudget(
            max_prompt_tokens=custom_max,
            max_message_tokens=max(12_000, custom_max * 3 // 4),
        )
    elif is_ultra:
        ultra_prompt = min(64_000, max(20_000, profile.context_window))
        ultra_message = min(48_000, max(12_000, ultra_prompt * 3 // 4))
        base = PromptBudget(
            max_prompt_tokens=ultra_prompt,
            max_message_tokens=ultra_message,
        )
    else:
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
        self.context_actions = _RootContextActions(self) if default.context_actions is not None else None

    def _assembly_for_root(self, root):
        if root not in self._builders:
            self._builders[root] = self._factory._build(
                self._mode, self._client, self._profile, root)
        return self._builders[root]

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
        builder = self._assembly_for_root(active_root)
        if request is not None:
            return await builder.build(request)
        return await builder.build(
            active_thread_id, messages, user_input, tools, task_state, cancellation
        )

    async def compact_context(
        self,
        thread_id: str,
        cancellation: object = None,
    ) -> object:
        root = self._factory._workspace_runtime.root_for_thread(thread_id)
        if root is None:
            await self._factory._workspace_runtime.hydrate_bindings()
            root = self._factory._workspace_runtime.root_for_thread(thread_id)
        active_root = root or self._factory._root
        builder = self._assembly_for_root(active_root)
        compact = getattr(builder, "compact_context", None)
        if not callable(compact):
            raise RuntimeError("semantic context compaction is unavailable")
        return await compact(thread_id, cancellation)

    def semantic_snapshot_for_root(self, root: Path) -> object:
        active_root = Path(root).resolve()
        builder = self._assembly_for_root(active_root)
        provider = builder.semantic_snapshot
        if not callable(provider):
            raise RuntimeError("semantic snapshot is unavailable")
        return provider(active_root)


class _RootContextActions:
    """Route stateful context actions to the same workspace builder as the turn."""
    def __init__(self, builder):
        self.builder = builder
        self.default = builder._builders[builder._factory._root].context_actions
        self.names = self.default.names

    def definitions(self):
        return self.default.definitions()

    async def dispatch(self, request, cancellation):
        thread = self.builder._factory._thread_binding.current()
        runtime = self.builder._factory._workspace_runtime
        root = runtime.root_for_thread(thread)
        if root is None:
            await runtime.hydrate_bindings()
            root = runtime.root_for_thread(thread)
        assembly = self.builder._assembly_for_root(root or self.builder._factory._root)
        return await assembly.context_actions.dispatch(request, cancellation)


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
    action_lineage: ActionLineage | None = None,
    inherited_authorization: TaskAuthorization | None = None,
    source_completion=None,
) -> AgentEngine:
    # Inherit the Host's existing path capability, never mobile/model arguments.
    verification_allow_sensitive = getattr(dispatcher,
        "verification_allow_sensitive_paths", False)
    review_model = None
    if isinstance(context, ContextAssembly):
        review_model = context.parent_review_model if action_lineage is None else None
        model = context.model_client
        dispatcher = ContextScopedDispatcher(dispatcher, context.context_actions)
        snapshot = context.semantic_snapshot
        context = context.builder
    else:
        snapshot = getattr(context, "semantic_snapshot_for_root", None)
    mode_limits = mode.definition.limits
    limits = EngineLimits(
        min(profile.max_agent_rounds, mode_limits.max_agent_rounds),
        min(profile.max_tool_calls, mode_limits.max_tool_calls),
        min(
            profile.max_tool_calls_per_round,
            mode_limits.max_tool_calls_per_round,
        ),
        profile.context_policy.task_tokens if profile.context_policy is not None else mode_limits.max_total_tokens,
        mode_limits.max_assistant_chars,
    )
    verification_enabled = structured_verification_enabled()
    from .parent_review import ParentSourceReview
    return AgentEngine(
        model,
        context,
        dispatcher,
        sessions,
        limits=limits,
        model_name=profile.provider.model,
        verification=TaskScopedVerificationService(
            sessions,
            snapshot,
            enable_structured_verification=verification_enabled,
            allow_sensitive_paths=verification_allow_sensitive,
        ),
        require_verification=verification_enabled,
        peer_tool_names=("list_agents", "send_message"),
        capability_strategy=capability_strategy,
        action_lineage=action_lineage,
        inherited_authorization=inherited_authorization,
        source_completion=source_completion,
        parent_review=ParentSourceReview(sessions, dispatcher,
            review_model_identity=review_model.identity if review_model is not None else None)
            if action_lineage is None else None,
        parent_review_model=review_model,
    )
