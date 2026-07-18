from __future__ import annotations

import asyncio
import json
import re
from typing import Sequence

from code_agent.core.models import ContextBundle, Message, ToolDefinition
from code_agent.core.task_state import TaskState

from .compaction import DeterministicCompactor
from .errors import ContextBudgetError, RuleLimitError
from .models import ContextConfig
from .repo_map import RepoMapBuilder
from .rules import RuleLoader
from .tokens import estimate_tokens
from .task_state import render_task_state


_GREETING = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "你好",
        "您好",
        "在吗",
        "谢谢",
        "早上好",
        "下午好",
        "晚上好",
    }
)
_GREETING_PUNCTUATION = re.compile(r"[\s!！?？,.，。]+")


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

    async def build(
        self,
        messages: Sequence[Message],
        user_input: str,
        tools: Sequence[ToolDefinition],
        task_state: TaskState,
    ) -> ContextBundle:
        """Build a stable prompt and compacted messages for one model turn."""
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
        return await asyncio.to_thread(
            self._build_sync, checked, user_input, checked_tools, task_state
        )

    def _build_sync(
        self,
        messages: tuple[Message, ...],
        user_input: str,
        tools: tuple[ToolDefinition, ...],
        task_state: TaskState,
    ) -> ContextBundle:
        working = messages
        if user_input:
            working += (Message(role="user", content=user_input),)
        rendered_rules = self.rules.render(self.rules.load())
        rule_tokens = estimate_tokens(rendered_rules)
        if rule_tokens > self.config.prompt_budget.max_rule_tokens:
            raise RuleLimitError(
                f"project rules exceed {self.config.prompt_budget.max_rule_tokens:,} tokens"
            )
        rendered_tools = _render_tools(tools)
        rendered_state = render_task_state(
            task_state, self.config.prompt_budget.max_task_state_tokens
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
        query = user_input or _latest_user_text(compacted.messages)
        if self.config.repo_map_enabled and _requires_repo_map(query):
            rendered_map, cache_hits, cache_misses = (
                self.repo_map.render_with_metrics(
                    query,
                    _touched_files(task_state),
                    allocation.repo_map_tokens,
                )
            )
        else:
            rendered_map, cache_hits, cache_misses = "", 0, 0
        system_prompt = prefix + rendered_map
        prompt_tokens = (
            estimate_tokens(system_prompt)
            + estimate_tokens(rendered_tools)
            + _message_tokens(compacted.messages)
        )
        if prompt_tokens > (
            self.config.prompt_budget.max_prompt_tokens
            - self.config.prompt_budget.safety_tokens
        ):
            raise ContextBudgetError("rendered prompt exceeds its token budget")
        return ContextBundle(
            system_prompt=system_prompt,
            messages=compacted.messages,
            measurements={
                "prompt_tokens": self.config.prompt_budget.max_prompt_tokens,
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


def _requires_repo_map(query: str) -> bool:
    normalized = _GREETING_PUNCTUATION.sub("", query).casefold()
    return normalized not in _GREETING


def _touched_files(task_state: TaskState) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*task_state.files_changed, *task_state.files_read)
        )
    )


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
