from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Iterable


class ApprovalMode(str, Enum):
    PLAN = "plan"
    ASK = "ask"
    AUTO = "auto"


class Capability(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    OUTSIDE_WORKSPACE = "outside_workspace"


class DecisionOutcome(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PolicyDecision:
    outcome: DecisionOutcome
    risk: RiskLevel
    reason: str
    capabilities: FrozenSet[Capability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DecisionOutcome):
            raise TypeError("outcome must be a DecisionOutcome")
        if not isinstance(self.risk, RiskLevel):
            raise TypeError("risk must be a RiskLevel")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        capabilities: Iterable[Capability] = self.capabilities
        frozen = frozenset(capabilities)
        if not all(isinstance(item, Capability) for item in frozen):
            raise TypeError("capabilities must contain only Capability values")
        object.__setattr__(self, "capabilities", frozen)
