from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from code_agent.core.limits import EngineLimits


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def _text(value: object, label: str, maximum: int = 4_096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-blank text of at most {maximum} characters")
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, 64)
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{label} must be a stable lowercase identifier")
    return text


def _agent_identifier(value: object) -> str:
    text = _text(value, "agent_id", 128)
    if not _TOOL_NAME.fullmatch(text):
        raise ValueError("agent_id must be a stable identifier or namespace")
    return text


def _nonnegative(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive(value: object, label: str) -> int:
    result = _nonnegative(value, label)
    if result == 0:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _names(values: object, label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TypeError(f"{label} must be a sequence")
    result = tuple(_text(value, label, 128) for value in values)
    if any(not _TOOL_NAME.fullmatch(value) for value in result):
        raise ValueError(f"{label} must contain stable tool names")
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    return result


class AgentMode(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"


class ReasoningEffort(str, Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class AgentRole(str, Enum):
    MAIN = "main"
    SUBAGENT = "subagent"
    ORACLE = "oracle"
    REVIEW = "review"
    SEARCH = "search"
    LIBRARIAN = "librarian"
    CUSTOM = "custom"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ModeDefinition:
    mode: AgentMode
    profile_id: str
    prompt_policy: str
    tool_names: tuple[str, ...]
    reasoning_effort: ReasoningEffort
    limits: EngineLimits
    description: str
    oracle_profile_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AgentMode):
            raise TypeError("mode must be an AgentMode")
        object.__setattr__(self, "profile_id", _identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "prompt_policy", _identifier(self.prompt_policy, "prompt_policy"))
        object.__setattr__(self, "tool_names", _names(self.tool_names, "tool_names"))
        if not isinstance(self.reasoning_effort, ReasoningEffort):
            raise TypeError("reasoning_effort must be a ReasoningEffort")
        if not isinstance(self.limits, EngineLimits):
            raise TypeError("limits must be EngineLimits")
        object.__setattr__(self, "description", _text(self.description, "description", 512))
        if self.oracle_profile_id is not None:
            object.__setattr__(self, "oracle_profile_id", _identifier(self.oracle_profile_id, "oracle_profile_id"))


@dataclass(frozen=True)
class ModeSnapshot:
    definition: ModeDefinition
    model: str
    oracle_model: str | None
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ModeDefinition):
            raise TypeError("definition must be a ModeDefinition")
        object.__setattr__(self, "model", _text(self.model, "model", 512))
        if self.oracle_model is not None:
            object.__setattr__(self, "oracle_model", _text(self.oracle_model, "oracle_model", 512))
        if not isinstance(self.digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise ValueError("digest must be a SHA-256 hex digest")


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    role: AgentRole
    mode: ModeSnapshot
    instructions: str
    tool_names: tuple[str, ...] = ()
    may_write: bool = False
    max_children: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _agent_identifier(self.agent_id))
        if not isinstance(self.role, AgentRole):
            raise TypeError("role must be an AgentRole")
        if not isinstance(self.mode, ModeSnapshot):
            raise TypeError("mode must be a ModeSnapshot")
        object.__setattr__(self, "instructions", _text(self.instructions, "instructions"))
        tools = _names(self.tool_names, "tool_names")
        allowed = set(self.mode.definition.tool_names)
        if any(name not in allowed for name in tools):
            raise ValueError("agent tools must be a subset of mode tools")
        object.__setattr__(self, "tool_names", tools)
        if not isinstance(self.may_write, bool):
            raise TypeError("may_write must be a boolean")
        object.__setattr__(self, "max_children", _nonnegative(self.max_children, "max_children"))

    @property
    def effective_tools(self) -> tuple[str, ...]:
        return self.tool_names or self.mode.definition.tool_names

    @property
    def advisory(self) -> bool:
        return self.role is not AgentRole.MAIN


@dataclass(frozen=True)
class AgentUsage:
    total_tokens: int = 0
    tool_calls: int = 0
    active_seconds: int = 0

    def __post_init__(self) -> None:
        for name in ("total_tokens", "tool_calls", "active_seconds"):
            object.__setattr__(self, name, _nonnegative(getattr(self, name), name))


@dataclass(frozen=True)
class ChildRunRequest:
    parent_run_id: str
    objective: str
    agent: AgentDefinition
    depth: int
    token_budget: int
    tool_budget: int
    active_seconds: int
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 128))
        object.__setattr__(self, "parent_run_id", _text(self.parent_run_id, "parent_run_id", 128))
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        if not isinstance(self.agent, AgentDefinition):
            raise TypeError("agent must be an AgentDefinition")
        object.__setattr__(self, "depth", _positive(self.depth, "depth"))
        object.__setattr__(self, "token_budget", _positive(self.token_budget, "token_budget"))
        object.__setattr__(self, "tool_budget", _nonnegative(self.tool_budget, "tool_budget"))
        object.__setattr__(self, "active_seconds", _positive(self.active_seconds, "active_seconds"))


@dataclass(frozen=True)
class AgentReference:
    source: str
    identifier: str
    excerpt: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _identifier(self.source, "source"))
        object.__setattr__(self, "identifier", _text(self.identifier, "identifier", 1_024))
        if not isinstance(self.excerpt, str) or len(self.excerpt) > 2_048:
            raise ValueError("excerpt must be bounded text")


@dataclass(frozen=True)
class ChildRunResult:
    run_id: str
    status: RunStatus
    summary: str
    usage: AgentUsage = field(default_factory=AgentUsage)
    references: tuple[AgentReference, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 128))
        if not isinstance(self.status, RunStatus):
            raise TypeError("status must be a RunStatus")
        if not isinstance(self.summary, str) or len(self.summary) > 16_384:
            raise ValueError("summary must be bounded text")
        if not isinstance(self.usage, AgentUsage):
            raise TypeError("usage must be AgentUsage")
        references = tuple(self.references)
        if any(not isinstance(value, AgentReference) for value in references):
            raise TypeError("references must contain AgentReference values")
        object.__setattr__(self, "references", references)
        if self.error is not None:
            object.__setattr__(self, "error", _text(self.error, "error", 2_048))
        if self.status is RunStatus.COMPLETED and not self.summary.strip():
            raise ValueError("completed result requires a summary")


@dataclass(frozen=True)
class RunView:
    run_id: str
    parent_run_id: str
    agent_id: str
    role: AgentRole
    mode: AgentMode
    status: RunStatus
    may_write: bool
    objective: str
    usage: AgentUsage = field(default_factory=AgentUsage)

    @classmethod
    def from_request(cls, request: ChildRunRequest, status: RunStatus) -> "RunView":
        return cls(
            request.run_id,
            request.parent_run_id,
            request.agent.agent_id,
            request.agent.role,
            request.agent.mode.definition.mode,
            status,
            request.agent.may_write,
            request.objective,
        )

    def with_result(self, result: ChildRunResult) -> "RunView":
        return RunView(
            self.run_id,
            self.parent_run_id,
            self.agent_id,
            self.role,
            self.mode,
            result.status,
            self.may_write,
            self.objective,
            result.usage,
        )

