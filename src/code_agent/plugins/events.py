from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from code_agent.core._json import JSONValue, freeze_mapping, validate_json_mapping

from .models import ActionProposal, UiRequest
from .registry import PluginHost


@dataclass(frozen=True)
class EventProjection:
    kind: str
    task_id: str
    fields: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind must be non-blank")
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be non-blank")
        validate_json_mapping(self.fields, "fields")
        forbidden = {"api_key", "authorization", "reasoning", "raw_tool_output"}
        if forbidden & {key.casefold() for key in self.fields}:
            raise ValueError("event projection contains a sensitive field")
        object.__setattr__(self, "fields", freeze_mapping(self.fields, "fields"))


@dataclass(frozen=True)
class PluginProposal:
    plugin_id: str
    subscription_id: str
    event_kind: str
    ui: UiRequest | None = None
    action: ActionProposal | None = None


class DeclarativeEventRouter:
    def __init__(self, host: PluginHost, *, max_proposals: int = 32, max_depth: int = 2) -> None:
        if not isinstance(host, PluginHost):
            raise TypeError("host must be PluginHost")
        if max_proposals <= 0 or max_depth <= 0:
            raise ValueError("router bounds must be positive")
        self._host = host
        self._max_proposals = max_proposals
        self._max_depth = max_depth

    def route(self, event: EventProjection, *, depth: int = 0) -> tuple[PluginProposal, ...]:
        if not isinstance(event, EventProjection):
            raise TypeError("event must be EventProjection")
        if depth >= self._max_depth:
            return ()
        proposals: list[PluginProposal] = []
        for registered in self._host.contributions("event"):
            subscription = registered.value
            if event.kind not in subscription.event_kinds:
                continue
            proposals.append(
                PluginProposal(
                    registered.plugin_id,
                    subscription.identifier,
                    event.kind,
                    subscription.ui,
                    subscription.action,
                )
            )
            if len(proposals) >= self._max_proposals:
                break
        return tuple(proposals)
