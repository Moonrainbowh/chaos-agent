from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from code_agent.core.models import ActionRequest
from code_agent.core.task import TaskAuthorization

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

    def evaluate(self, request: ActionRequest, task_authorization: TaskAuthorization | None = None) -> PolicyDecision:
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

        if Capability.PROTECTED_PATH in classified.capabilities:
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required for a protected path",
            )

        read_only = (
            classified.capabilities - frozenset({Capability.OUTSIDE_WORKSPACE})
            == frozenset({Capability.READ})
        )
        mode = self.config.approval_mode
        outside = Capability.OUTSIDE_WORKSPACE in classified.capabilities

        if mode is ApprovalMode.PLAN:
            outcome = DecisionOutcome.ALLOW if read_only and not outside else DecisionOutcome.DENY
            reason = (
                "allowed because plan mode permits read-only actions"
                if read_only and not outside
                else "denied because plan mode permits read-only actions only"
            )
            return self._decision(outcome, classified, reason)

        if task_authorization is not None:
            configured = self.config.workspace_root
            authorized_root = Path(task_authorization.workspace_root).resolve(strict=False)
            local = configured is not None and configured == authorized_root
            blocked = Capability.NETWORK in classified.capabilities or Capability.OUTSIDE_WORKSPACE in classified.capabilities
            if local and not blocked:
                if Capability.RAW_SHELL in classified.capabilities:
                    return self._decision(
                        DecisionOutcome.ASK,
                        classified,
                        "approval required for model-provided raw shell text",
                    )
                if Capability.VERIFICATION in classified.capabilities and task_authorization.allow_local_execute:
                    return self._decision(DecisionOutcome.ALLOW, classified, "allowed by foreground task authorization")
                if Capability.WRITE in classified.capabilities and task_authorization.allow_workspace_write:
                    return self._decision(DecisionOutcome.ALLOW, classified, "allowed by foreground task authorization")
                if Capability.READ in classified.capabilities:
                    return self._decision(DecisionOutcome.ALLOW, classified, "allowed by foreground task authorization")

        if mode is ApprovalMode.ASK:
            if outside:
                outcome = DecisionOutcome.ASK if read_only else DecisionOutcome.DENY
                reason = (
                    "approval required for a single read outside the workspace"
                    if read_only
                    else "denied because ask mode does not permit writes outside the workspace"
                )
                return self._decision(outcome, classified, reason)
            outcome = DecisionOutcome.ALLOW if read_only else DecisionOutcome.ASK
            reason = (
                "allowed because the action is read-only"
                if read_only
                else "approval required by ask mode for a non-read action"
            )
            return self._decision(outcome, classified, reason)

        if mode is ApprovalMode.FULL_LOCAL:
            if Capability.EXECUTE in classified.capabilities:
                return self._decision(
                    DecisionOutcome.ASK,
                    classified,
                    "approval required for every command execution",
                )
            return self._decision(
                DecisionOutcome.ALLOW,
                classified,
                "allowed by full-local mode for a non-critical typed action",
            )

        if outside:
            return self._decision(
                DecisionOutcome.ASK,
                classified,
                "approval required for access outside the workspace",
            )
        if read_only:
            return self._decision(
                DecisionOutcome.ALLOW, classified, "allowed because the action is read-only"
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
