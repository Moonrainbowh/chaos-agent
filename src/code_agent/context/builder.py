from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import ceil
from typing import Sequence

from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, ToolDefinition
from code_agent.core.cancellation import CancellationToken
from code_agent.core.task_state import TaskState
from code_agent.thread_intelligence.compaction import SemanticCompactionResult

from .budget import PromptAllocation
from .compaction import DeterministicCompactor
from .errors import ContextBudgetError, RuleLimitError
from .models import CompactionResult, ContextConfig
from .repo_map import RepoMapBuilder
from .repo_index import RepoIndexSnapshot
from .rules import RuleLoader
from .semantic import SemanticCompactor, compact_with_cancellation
from .tokens import estimate_tokens
from .task_state import render_task_state
from ._builder_support import (
    _context_bundle,
    _latest_user_text,
    _message_tokens,
    _render_tools,
    _requires_repo_map,
    _system_prefix,
    _touched_files,
)


@dataclass(frozen=True)
class _BuildPlan:
    working: tuple[Message, ...]
    working_tokens: int
    prefix: str
    rendered_tools: str
    allocation: PromptAllocation


class WorkspaceContextBuilder:
    """Build bounded model context from guarded workspace state."""

    def __init__(
        self,
        config: ContextConfig,
        rules: RuleLoader,
        repo_map: RepoMapBuilder,
        compactor: DeterministicCompactor,
        *,
        semantic_compactor: SemanticCompactor | None = None,
    ) -> None:
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        if not isinstance(rules, RuleLoader):
            raise TypeError("rules must be a RuleLoader")
        if not isinstance(repo_map, RepoMapBuilder):
            raise TypeError("repo_map must be a RepoMapBuilder")
        if not isinstance(compactor, DeterministicCompactor):
            raise TypeError("compactor must be a DeterministicCompactor")
        if semantic_compactor is not None and not callable(
            getattr(semantic_compactor, "compact", None)
        ):
            raise TypeError("semantic_compactor must provide compact")
        if (
            rules.config != config
            or repo_map.config != config
            or compactor.config != config
        ):
            raise ValueError("all context components must share config")
        self.config = config
        self.rules = rules
        self.repo_map = repo_map
        self.compactor = compactor
        self.semantic_compactor = semantic_compactor

    def semantic_snapshot_for_turn(self) -> RepoIndexSnapshot:
        """Return the exact immutable repository snapshot used by Repo Map."""
        return self.repo_map.index.snapshot_for_turn()

    async def build(
        self,
        thread_id: str | ContextRequest,
        messages: Sequence[Message] | None = None,
        user_input: str | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        task_state: TaskState | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ContextBundle:
        """Build a stable prompt and compacted messages for one model turn."""
        request = self._request_from_arguments(
            thread_id, messages, user_input, tools, task_state, cancellation
        )
        plan = await asyncio.to_thread(self._prepare_sync, request)
        semantic = await self._compact_semantic(request, plan)
        bundle = await asyncio.to_thread(
            self._finish_sync, request, plan, semantic
        )
        request.cancellation.raise_if_cancelled()
        return bundle

    @staticmethod
    def _request_from_arguments(
        thread_id: str | ContextRequest,
        messages: Sequence[Message] | None,
        user_input: str | None,
        tools: Sequence[ToolDefinition] | None,
        task_state: TaskState | None,
        cancellation: CancellationToken | None,
    ) -> ContextRequest:
        if isinstance(thread_id, ContextRequest):
            if any(
                value is not None
                for value in (messages, user_input, tools, task_state, cancellation)
            ):
                raise TypeError("ContextRequest cannot be combined with legacy arguments")
            return thread_id
        if not isinstance(thread_id, str):
            raise TypeError("context request must be a ContextRequest or legacy arguments")
        if not thread_id.strip():
            raise ValueError("thread_id must be non-blank text")
        if messages is None or user_input is None or tools is None:
            raise TypeError("legacy context arguments are incomplete")
        if task_state is None:
            raise TypeError("task_state is required")
        if cancellation is None:
            raise TypeError("cancellation is required")
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        cancellation.raise_if_cancelled()
        checked = tuple(messages)
        if not all(isinstance(message, Message) for message in checked):
            raise TypeError("messages must contain only Message values")
        if not isinstance(user_input, str):
            raise TypeError("user_input must be text")
        checked_tools = tuple(tools)
        if not all(isinstance(tool, ToolDefinition) for tool in checked_tools):
            raise TypeError("tools must contain only ToolDefinition values")
        if not isinstance(task_state, TaskState):
            raise TypeError("task_state must be a TaskState")
        return ContextRequest(
            thread_id=thread_id,
            revision=1,
            messages=checked,
            user_input=user_input,
            tools=checked_tools,
            task_state=task_state,
            cancellation=cancellation,
        )

    def _prepare_sync(self, request: ContextRequest) -> _BuildPlan:
        request.cancellation.raise_if_cancelled()
        working = request.messages
        if request.user_input or request.attachments:
            working += (
                Message(
                    role="user",
                    content=request.user_input,
                    attachments=request.attachments,
                ),
            )
        working_tokens = _message_tokens(working)
        rendered_rules = self.rules.render(self.rules.load())
        request.cancellation.raise_if_cancelled()
        rule_tokens = estimate_tokens(rendered_rules)
        if rule_tokens > self.config.prompt_budget.max_rule_tokens:
            raise RuleLimitError(
                f"project rules exceed {self.config.prompt_budget.max_rule_tokens:,} tokens"
            )
        rendered_tools = _render_tools(request.tools)
        rendered_state = render_task_state(
            request.task_state, self.config.prompt_budget.max_task_state_tokens
        )
        state_tokens = estimate_tokens(rendered_state)
        if state_tokens > self.config.prompt_budget.max_task_state_tokens:
            raise ContextBudgetError("task state exceeds its configured token ceiling")
        prefix = _system_prefix(
            self.config.system_prompt, rendered_rules, rendered_state
        )
        allocation = self.config.prompt_budget.allocate(
            system_and_rules_tokens=estimate_tokens(
                _system_prefix(self.config.system_prompt, rendered_rules, "")
            ),
            tool_tokens=estimate_tokens(rendered_tools),
            task_state_tokens=state_tokens,
        )
        return _BuildPlan(working, working_tokens, prefix, rendered_tools, allocation)

    async def _compact_semantic(
        self, request: ContextRequest, plan: _BuildPlan
    ) -> SemanticCompactionResult | None:
        if self.semantic_compactor is None:
            return None
        context_limit = plan.allocation.message_tokens
        context_tokens = plan.working_tokens
        if request.context_pressure is not None:
            context_tokens = max(
                context_tokens, ceil(request.context_pressure * context_limit)
            )
        return await compact_with_cancellation(
            self.semantic_compactor,
            request.thread_id,
            request.revision,
            plan.working,
            context_tokens=context_tokens,
            context_limit=context_limit,
            target_tokens=plan.allocation.message_tokens,
            cancellation=request.cancellation,
        )

    def _finish_sync(
        self,
        request: ContextRequest,
        plan: _BuildPlan,
        semantic: SemanticCompactionResult | None,
    ) -> ContextBundle:
        request.cancellation.raise_if_cancelled()
        messages = plan.working if semantic is None else semantic.messages
        compacted = self.compactor.compact(messages, plan.allocation.message_tokens)
        query = request.user_input or _latest_user_text(compacted.messages)
        request.cancellation.raise_if_cancelled()
        if self.config.repo_map_enabled and _requires_repo_map(query):
            rendered_map, cache_hits, cache_misses = (
                self.repo_map.render_with_metrics(
                    query,
                    _touched_files(request.task_state),
                    plan.allocation.repo_map_tokens,
                )
            )
        else:
            rendered_map, cache_hits, cache_misses = "", 0, 0
        request.cancellation.raise_if_cancelled()
        system_prompt = plan.prefix + rendered_map
        return _context_bundle(
            self.config,
            system_prompt,
            plan.rendered_tools,
            compacted,
            plan.allocation,
            cache_hits,
            cache_misses,
            semantic,
        )
