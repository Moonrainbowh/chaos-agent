from __future__ import annotations

from typing import Sequence

from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message
from code_agent.skills.registry import SkillActivation, SkillContextBuilder
from code_agent.thread_intelligence.compaction import (
    SemanticCompactionResult,
    SemanticCompactor,
)
from code_agent.thread_intelligence.deterministic_summary import (
    DeterministicSummaryService,
)
from code_agent.thread_intelligence.context_builder import ThreadAwareContextBuilder
from code_agent.thread_intelligence.models import semantic_checkpoint_payload


class PersistingAnchoredCompactor:
    """Persist redacted facts after successful semantic compaction."""

    def __init__(self, inner: object, sessions: object) -> None:
        if not callable(getattr(inner, "compact", None)):
            raise TypeError("inner must provide compact")
        if not callable(getattr(sessions, "create_checkpoint", None)):
            raise TypeError("sessions must provide create_checkpoint")
        self._inner = inner
        self._sessions = sessions

    async def compact(
        self,
        thread_id: str,
        revision: int,
        messages: Sequence[Message],
        *,
        context_tokens: int,
        context_limit: int,
        target_tokens: int,
        cancellation: CancellationToken | None = None,
    ) -> SemanticCompactionResult:
        result = await self._inner.compact(
            thread_id,
            revision,
            messages,
            context_tokens=context_tokens,
            context_limit=context_limit,
            target_tokens=target_tokens,
            cancellation=cancellation,
        )
        if not isinstance(result, SemanticCompactionResult):
            raise TypeError("inner must return SemanticCompactionResult")
        checkpoint = result.checkpoint
        if checkpoint is None:
            return result
        if checkpoint.thread_id != thread_id:
            raise ValueError("checkpoint thread mismatch")
        await self._sessions.create_checkpoint(
            checkpoint.thread_id,
            "semantic-compaction",
            semantic_checkpoint_payload(checkpoint),
        )
        return result


def build_context_runtime(
    config: ContextConfig,
    rules: RuleLoader,
    repo_map: RepoMapBuilder,
    skills: SkillActivation | object,
    sessions: object,
    *,
    summarizer: object | None = None,
    context_limit: int | None = None,
    target_tokens: int | None = None,
    summary_tokens: int = 1_024,
    model_token_budget: int = 8_192,
) -> object:
    """Compose production thread intelligence or the legacy local pipeline."""
    deterministic = DeterministicCompactor(config)
    if summarizer is not None:
        if context_limit is None or target_tokens is None:
            raise TypeError("production context limits are required")
        semantic = SemanticCompactor(  # type: ignore[arg-type]
            summarizer,
            deterministic,
            summary_tokens=summary_tokens,
            model_token_budget=model_token_budget,
        )
        workspace = WorkspaceContextBuilder(
            config,
            rules,
            repo_map,
            deterministic,
        )
        return ThreadAwareContextBuilder(
            sessions,
            semantic,
            workspace,
            context_limit=context_limit,
            target_tokens=target_tokens,
        )
    if context_limit is not None or target_tokens is not None:
        raise TypeError("summarizer is required for production context limits")

    semantic = SemanticCompactor(DeterministicSummaryService(), deterministic)
    persisting = PersistingAnchoredCompactor(semantic, sessions)
    workspace = WorkspaceContextBuilder(
        config,
        rules,
        repo_map,
        deterministic,
        semantic_compactor=persisting,
    )
    return SkillContextBuilder(workspace, skills)
