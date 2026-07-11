from __future__ import annotations

import asyncio
from typing import Sequence

from code_agent.core.models import ContextBundle, Message

from .compaction import DeterministicCompactor
from .models import ContextConfig
from .repo_map import RepoMapBuilder
from .rules import RuleLoader


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
        self, messages: Sequence[Message], user_input: str
    ) -> ContextBundle:
        """Build a stable prompt and compacted messages for one model turn."""
        checked = tuple(messages)
        if not all(isinstance(message, Message) for message in checked):
            raise TypeError("messages must contain only Message values")
        if not isinstance(user_input, str):
            raise TypeError("user_input must be text")
        return await asyncio.to_thread(self._build_sync, checked, user_input)

    def _build_sync(
        self, messages: tuple[Message, ...], user_input: str
    ) -> ContextBundle:
        working = messages
        if user_input:
            working += (Message(role="user", content=user_input),)
        compacted = self.compactor.compact(working)
        query = user_input or _latest_user_text(compacted.messages)
        rendered_rules = self.rules.render(self.rules.load())
        rendered_map = self.repo_map.render(
            query,
            (),
            self.config.repo_map_tokens,
        )
        sections = [self.config.system_prompt]
        if rendered_rules:
            sections.append(rendered_rules)
        if rendered_map:
            sections.append("Repository map:\n" + rendered_map)
        return ContextBundle(
            system_prompt="\n\n".join(sections),
            messages=compacted.messages,
        )


def _latest_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""
