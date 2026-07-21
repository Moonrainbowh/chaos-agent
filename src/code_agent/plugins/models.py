from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from code_agent.core._json import JSONValue, freeze_mapping, validate_json_mapping
from code_agent.orchestration.models import AgentMode, ReasoningEffort


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_QUALIFIED = re.compile(r"^[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_.-]{0,127})+$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, label: str, maximum: int = 4_096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-blank bounded text")
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, 64)
    if not _ID.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase identifier")
    return text


class PluginRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    CRITICAL = "critical"


class UiPrimitive(str, Enum):
    NOTIFY = "notify"
    CONFIRM = "confirm"
    INPUT = "input"
    SELECT = "select"


@dataclass(frozen=True)
class ToolContribution:
    identifier: str
    description: str
    target: str
    risk: PluginRisk
    input_schema: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "tool identifier"))
        object.__setattr__(self, "description", _text(self.description, "description", 1_024))
        target = _text(self.target, "target", 192)
        if not (_QUALIFIED.fullmatch(target) or _ID.fullmatch(target)):
            raise ValueError("tool target must be a typed action or MCP namespace")
        if target.startswith(("shell.", "python.", "http.", "https.")):
            raise ValueError("dynamic execution targets are forbidden")
        object.__setattr__(self, "target", target)
        if not isinstance(self.risk, PluginRisk):
            raise TypeError("risk must be PluginRisk")
        validate_json_mapping(self.input_schema, "input_schema")
        object.__setattr__(self, "input_schema", freeze_mapping(self.input_schema, "input_schema"))


@dataclass(frozen=True)
class CommandContribution:
    identifier: str
    description: str
    controller: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "command identifier"))
        object.__setattr__(self, "description", _text(self.description, "description", 1_024))
        object.__setattr__(self, "controller", _identifier(self.controller, "controller"))


@dataclass(frozen=True)
class ModeContribution:
    identifier: str
    base_mode: AgentMode
    prompt_policy: str
    tool_names: tuple[str, ...]
    reasoning_effort: ReasoningEffort | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "mode identifier"))
        if not isinstance(self.base_mode, AgentMode):
            raise TypeError("base_mode must be AgentMode")
        object.__setattr__(self, "prompt_policy", _identifier(self.prompt_policy, "prompt_policy"))
        tools = tuple(self.tool_names)
        if any(not isinstance(name, str) or not name.strip() for name in tools):
            raise ValueError("tool_names must contain non-blank names")
        if len(tools) != len(set(tools)):
            raise ValueError("tool_names must not contain duplicates")
        object.__setattr__(self, "tool_names", tools)
        if self.reasoning_effort is not None and not isinstance(self.reasoning_effort, ReasoningEffort):
            raise TypeError("reasoning_effort must be ReasoningEffort or None")


@dataclass(frozen=True)
class AgentContribution:
    identifier: str
    base_mode: AgentMode
    instructions: str
    tool_names: tuple[str, ...] = ()
    may_write: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "agent identifier"))
        if not isinstance(self.base_mode, AgentMode):
            raise TypeError("base_mode must be AgentMode")
        object.__setattr__(self, "instructions", _text(self.instructions, "instructions"))
        tools = tuple(self.tool_names)
        if any(not isinstance(name, str) or not name.strip() for name in tools):
            raise ValueError("tool_names must contain non-blank names")
        if len(tools) != len(set(tools)):
            raise ValueError("tool_names must not contain duplicates")
        object.__setattr__(self, "tool_names", tools)
        if not isinstance(self.may_write, bool):
            raise TypeError("may_write must be bool")


@dataclass(frozen=True)
class UiRequest:
    primitive: UiPrimitive
    title: str
    prompt: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.primitive, UiPrimitive):
            raise TypeError("primitive must be UiPrimitive")
        object.__setattr__(self, "title", _text(self.title, "title", 160))
        object.__setattr__(self, "prompt", _text(self.prompt, "prompt", 2_048))
        options = tuple(self.options)
        if len(options) > 20 or any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in options):
            raise ValueError("options must contain at most 20 bounded labels")
        if self.primitive is UiPrimitive.SELECT and not options:
            raise ValueError("select requires options")
        if self.primitive is not UiPrimitive.SELECT and options:
            raise ValueError("only select accepts options")
        object.__setattr__(self, "options", options)


@dataclass(frozen=True)
class ActionProposal:
    target: str
    arguments: Mapping[str, JSONValue] = field(default_factory=dict)
    risk: PluginRisk = PluginRisk.CRITICAL

    def __post_init__(self) -> None:
        target = _text(self.target, "target", 192)
        if not (_QUALIFIED.fullmatch(target) or _ID.fullmatch(target)):
            raise ValueError("proposal target must be a typed action")
        object.__setattr__(self, "target", target)
        validate_json_mapping(self.arguments, "arguments")
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments, "arguments"))
        if not isinstance(self.risk, PluginRisk):
            raise TypeError("risk must be PluginRisk")


@dataclass(frozen=True)
class EventSubscription:
    identifier: str
    event_kinds: tuple[str, ...]
    ui: UiRequest | None = None
    action: ActionProposal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "subscription identifier"))
        kinds = tuple(_identifier(kind, "event kind") for kind in self.event_kinds)
        if not kinds or len(kinds) != len(set(kinds)):
            raise ValueError("event_kinds must be non-empty and unique")
        object.__setattr__(self, "event_kinds", kinds)
        if (self.ui is None) == (self.action is None):
            raise ValueError("subscription must declare exactly one bounded proposal")


@dataclass(frozen=True)
class PluginContributions:
    tools: tuple[ToolContribution, ...] = ()
    commands: tuple[CommandContribution, ...] = ()
    modes: tuple[ModeContribution, ...] = ()
    agents: tuple[AgentContribution, ...] = ()
    events: tuple[EventSubscription, ...] = ()

    def __post_init__(self) -> None:
        for name, expected in (("tools", ToolContribution), ("commands", CommandContribution), ("modes", ModeContribution), ("agents", AgentContribution), ("events", EventSubscription)):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, expected) for value in values):
                raise TypeError(f"{name} contains an invalid contribution")
            identifiers = [value.identifier for value in values]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {name} contribution")
            object.__setattr__(self, name, values)


@dataclass(frozen=True)
class PluginManifest:
    identifier: str
    namespace: str
    version: str
    host_api: str
    digest: str
    source: str
    trusted: bool
    enabled: bool
    contributions: PluginContributions = field(default_factory=PluginContributions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _identifier(self.identifier, "plugin identifier"))
        object.__setattr__(self, "namespace", _identifier(self.namespace, "namespace"))
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise ValueError("version must be semantic version text")
        object.__setattr__(self, "host_api", _text(self.host_api, "host_api", 64))
        if not isinstance(self.digest, str) or not _DIGEST.fullmatch(self.digest):
            raise ValueError("digest must be a SHA-256 hex digest")
        object.__setattr__(self, "source", _text(self.source, "source", 1_024))
        if not isinstance(self.trusted, bool) or not isinstance(self.enabled, bool):
            raise TypeError("trusted and enabled must be bool")
        if not isinstance(self.contributions, PluginContributions):
            raise TypeError("contributions must be PluginContributions")
