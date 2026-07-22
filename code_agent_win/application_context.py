from __future__ import annotations

from pathlib import Path

from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.orchestration.models import ModeSnapshot
from code_agent.providers.config import ModelProfile
from code_agent.thread_intelligence.compaction import SemanticCompactor
from code_agent.thread_intelligence.context_builder import (
    ThreadAwareContextBuilder,
)
from code_agent.verification.task_service import (
    LedgerTaskVerificationService,
)
from code_agent_win.agent_modes import mode_prompt
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
            windows_system_prompt(self._git_available)
            + "\n\n"
            + mode_prompt(mode)
        )
        config = ContextConfig(
            root,
            root,
            prompt,
            repo_map_enabled=self._repo_map_enabled,
        )
        fallback = DeterministicCompactor(config)
        context = WorkspaceContextBuilder(
            config,
            RuleLoader(guard, files, config),
            RepoMapBuilder(
                files,
                config,
                index=repo_index,
                view_cache=self._repo_view_cache,
            ),
            fallback,
        )
        semantic = ThreadAwareContextBuilder(
            self._sessions,
            SemanticCompactor(
                ModelSemanticSummarizer(
                    client, profile.provider.model, self._sessions
                ),
                fallback,
            ),
            context,
            context_limit=profile.context_window,
            target_tokens=config.prompt_budget.max_message_tokens,
        )
        return BoundSkillContextBuilder(
            semantic, self._thread_binding, self._skills
        )

    def _workspace_parts(self, root: Path) -> tuple[object, object, object]:
        if root == self._root or self._workspace_runtime is None:
            return self._guard, self._files, self._repo_index
        services = self._workspace_runtime.services_for_root(root)
        return services.guard, services.files, services.repo_index


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
        self._inner = factory._build(mode, client, profile, factory._root)._inner

    async def build(
        self,
        thread_id: str,
        messages: object,
        user_input: str,
        tools: object,
        task_state: object,
        cancellation: object,
    ) -> object:
        root = self._factory._workspace_runtime.root_for_thread(thread_id)
        builder = self._factory._build(
            self._mode, self._client, self._profile, root or self._factory._root
        )
        return await builder.build(
            thread_id, messages, user_input, tools, task_state, cancellation
        )


def engine_for(
    model: object,
    profile: ModelProfile,
    context: object,
    dispatcher: object,
    sessions: object,
    workspace_root: Path,
    mode: ModeSnapshot,
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
