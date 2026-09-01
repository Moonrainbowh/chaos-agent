from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


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


class RuntimeReasoningEffort(str, Enum):
    """Provider-facing effort levels selectable independently of legacy modes."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class AgentTopology(str, Enum):
    SINGLE = "single"
    TEAM = "team"


@dataclass(frozen=True)
class RuntimeSelection:
    """One immutable, auditable provider/topology selection for a task."""

    topology: AgentTopology
    profile_id: str
    model: str
    api_protocol: str
    reasoning_effort: RuntimeReasoningEffort
    max_output_tokens: int
    legacy_mode: AgentMode
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.topology, AgentTopology):
            raise TypeError("topology must be an AgentTopology")
        object.__setattr__(
            self, "profile_id", _text(self.profile_id, "profile_id", 128)
        )
        object.__setattr__(self, "model", _text(self.model, "model", 512))
        protocol = _text(self.api_protocol, "api_protocol", 64)
        if not _IDENTIFIER.fullmatch(protocol):
            raise ValueError("api_protocol must be a stable lowercase identifier")
        object.__setattr__(self, "api_protocol", protocol)
        if not isinstance(self.reasoning_effort, RuntimeReasoningEffort):
            raise TypeError(
                "reasoning_effort must be a RuntimeReasoningEffort"
            )
        object.__setattr__(
            self,
            "max_output_tokens",
            _positive(self.max_output_tokens, "max_output_tokens"),
        )
        if not isinstance(self.legacy_mode, AgentMode):
            raise TypeError("legacy_mode must be an AgentMode")
        if not isinstance(self.digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.digest
        ):
            raise ValueError("digest must be a SHA-256 hex digest")


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(
            f"{label} must be non-blank text of at most {maximum} characters"
        )
    return value


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value
