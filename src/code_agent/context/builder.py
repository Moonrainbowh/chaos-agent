from __future__ import annotations

import asyncio
import json
from typing import Sequence

from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, ToolDefinition

from .budget import PromptAllocation
from .compaction import DeterministicCompactor
from .errors import ContextBudgetError, RuleLimitError
from .models import CompactionResult, ContextConfig
from .repo_map import RepoMapBuilder
from .rules import RuleLoader
from .tokens import estimate_tokens
from .task_state import render_task_state


class WorkspaceContextBuilder:
    """Build bounded model context from guarded workspace state."""

    def __init__(
        self,
        config: ContextConfig,
        rules: RuleLoader,
        repo_map: RepoMapBuilder,
        compactor: DeterministicCompactor,
    ) -> None:
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        if not isinstance(rules, RuleLoader):
            raise TypeError("rules must be a RuleLoader")
        if not isinstance(repo_map, RepoMapBuilder):
            raise TypeError("repo_map must be a RepoMapBuilder")
        if not isinstance(compactor, DeterministicCompactor):
            raise TypeError("compactor must be a DeterministicCompactor")
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

    async def build(self, request: ContextRequest) -> ContextBundle:
        """Build a stable prompt and compacted messages for one model turn."""
        if not isinstance(request, ContextRequest):
            raise TypeError("request must be a ContextRequest")
        request.cancellation.raise_if_cancelled()
        return await asyncio.to_thread(self._build_sync, request)

    def _build_sync(self, request: ContextRequest) -> ContextBundle:
        working = request.messages
        if request.user_input:
            working += (Message(role="user", content=request.user_input),)
        rendered_rules = self.rules.render(self.rules.load())
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
        compacted = self.compactor.compact(working, allocation.message_tokens)
        query = request.user_input or _latest_user_text(compacted.messages)
        rendered_map, cache_hits, cache_misses = self.repo_map.cache.measure_operation(
            lambda: self.repo_map.render(
                query,
                (),
                allocation.repo_map_tokens,
            )
        )
        system_prompt = prefix + rendered_map
        return _context_bundle(
            self.config,
            system_prompt,
            rendered_tools,
            compacted,
            allocation,
            cache_hits,
            cache_misses,
        )


def _context_bundle(
    config: ContextConfig,
    system_prompt: str,
    rendered_tools: str,
    compacted: CompactionResult,
    allocation: PromptAllocation,
    cache_hits: int,
    cache_misses: int,
) -> ContextBundle:
    prompt_tokens = (
        estimate_tokens(system_prompt)
        + estimate_tokens(rendered_tools)
        + _message_tokens(compacted.messages)
    )
    if prompt_tokens > (
        config.prompt_budget.max_prompt_tokens - config.prompt_budget.safety_tokens
    ):
        raise ContextBudgetError("rendered prompt exceeds its token budget")
    return ContextBundle(
        system_prompt=system_prompt,
        messages=compacted.messages,
        measurements={
            "prompt_tokens": config.prompt_budget.max_prompt_tokens,
            "rule_tokens": allocation.rule_tokens,
            "tool_tokens": allocation.tool_tokens,
            "task_state_tokens": allocation.task_state_tokens,
            "repo_map_tokens": allocation.repo_map_tokens,
            "message_tokens": allocation.message_tokens,
            "removed_message_count": compacted.removed_count,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
        },
    )


def _latest_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def _render_tools(tools: Sequence[ToolDefinition]) -> str:
    return "\n".join(
        json.dumps(
            tool.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        for tool in tools
    )


def _system_prefix(system_prompt: str, rules: str, task_state: str) -> str:
    sections = [system_prompt]
    if rules:
        sections.append(rules)
    if task_state:
        sections.append(task_state)
    sections.append("Repository map:\n")
    return "\n\n".join(sections)


def _message_tokens(messages: Sequence[Message]) -> int:
    total = 0
    for message in messages:
        total += 1 + estimate_tokens(message.content)
        if message.name:
            total += estimate_tokens(message.name)
        if message.tool_call_id:
            total += estimate_tokens(message.tool_call_id)
        for call in message.tool_calls:
            total += 1 + estimate_tokens(
                json.dumps(
                    call.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
    return total
