from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from code_agent.core.models import ActionRequest

from .classifier import ActionClassification, classify_action
from .models import (
    ApprovalMode,
    Capability,
    DecisionOutcome,
    PolicyDecision,
    RiskLevel,
)


@dataclass(frozen=True)
class PolicyConfig:
    approval_mode: ApprovalMode = ApprovalMode.ASK
    allow_network: bool = False
    workspace_root: Optional[Path] = None

    def __post_init__(self) -> None:
        if not isinstance(self.approval_mode, ApprovalMode):
            raise TypeError("approval_mode must be an ApprovalMode")
        if not isinstance(self.allow_network, bool):
            raise TypeError("allow_network must be a bool")
        if self.workspace_root is not None:
            if not isinstance(self.workspace_root, Path):
                raise TypeError("workspace_root must be a Path or None")
            object.__setattr__(
                self, "workspace_root", self.workspace_root.resolve(strict=False)
            )


@dataclass(frozen=True)
class ActionPolicy:
    config: PolicyConfig = field(default_factory=PolicyConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.config, PolicyConfig):
            raise TypeError("config must be a PolicyConfig")

    @staticmethod
    def _decision(
        outcome: DecisionOutcome,
        classified: ActionClassification,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            outcome=outcome,
            risk=classified.risk,
            reason=f"{reason}; classifier: {classified.reason}",
            capabilities=classified.capabilities,
        )

    def evaluate(self, request: ActionRequest) -> PolicyDecision:
        classified = classify_action(request, self.config.workspace_root)

        if not classified.known_tool:
            return self._decision(
                DecisionOutcome.DENY, classified, "denied because the tool is unknown"
            )
        if classified.risk is RiskLevel.CRITICAL:
            return self._decision(
                DecisionOutcome.DENY,
                classified,
                "denied because critical-risk actions are never approved",
            )

        read_only = classified.capabilities == frozenset({Capability.READ})
        mode = self.config.approval_mode

        if mode is ApprovalMode.PLAN:
            outcome = DecisionOutcome.ALLOW if read_only else DecisionOutcome.DENY
            reason = (
                "allowed because plan mode permits read-only actions"
                if read_only
                else "denied because plan mode permits read-only actions only"
            )
            return self._decision(outcome, classified, reason)

        if mode is ApprovalMode.ASK:
            outcome = DecisionOutcome.ALLOW if read_only else DecisionOutcome.ASK
            reason = (
                "allowed because the action is read-only"
                if read_only
                else "approval required by ask mode for a non-read action"
            )
            return self._decision(outcome, classified, reason)

        if read_only:
            return self._decision(
                DecisionOutcome.ALLOW, classified, "allowed because the action is read-only"
            )
        if Capability.OUTSIDE_WORKSPACE in classified.capabilities:
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required for access outside the workspace",
            )
        if Capability.EXECUTE in classified.capabilities:
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required for every command execution",
            )
        if classified.risk is RiskLevel.HIGH:
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required for a high-risk action",
            )
        if (
            Capability.NETWORK in classified.capabilities
            and not self.config.allow_network
        ):
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required because network access is disabled by configuration",
            )
        return self._decision(
            DecisionOutcome.ALLOW,
            classified,
            "allowed by auto mode for a recognized non-critical action",
        )
