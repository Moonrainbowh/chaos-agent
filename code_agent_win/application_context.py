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

    def __call__(
        self,
        mode: ModeSnapshot,
        client: object,
        profile: ModelProfile,
    ) -> object:
        prompt = (
            windows_system_prompt(self._git_available)
            + "\n\n"
            + mode_prompt(mode)
        )
        config = ContextConfig(
            self._root,
            self._root,
            prompt,
            repo_map_enabled=self._repo_map_enabled,
        )
        fallback = DeterministicCompactor(config)
        context = WorkspaceContextBuilder(
            config,
            RuleLoader(self._guard, self._files, config),
            RepoMapBuilder(
                self._files,
                config,
                index=self._repo_index,
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
        verification=LedgerTaskVerificationService(
            workspace_root, sessions
        ),
    )
