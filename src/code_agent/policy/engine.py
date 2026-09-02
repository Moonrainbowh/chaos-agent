from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from collections.abc import Mapping

from code_agent.core.models import ActionRequest
from code_agent.core.task import TaskAuthorization

from .classifier import (
    ActionClassification,
    classify_action,
    requires_explicit_edit_plan_approval,
)
from .models import (
    ApprovalMode,
    Capability,
    DecisionOutcome,
    PolicyDecision,
    RiskLevel,
)


@dataclass(frozen=True)
class PolicyConfig:
    approval_mode: ApprovalMode = ApprovalMode.AUTO
    allow_network: bool = False
    workspace_root: Optional[Path] = None
    mcp_risks: Mapping[str, str] = field(default_factory=dict)

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

    def evaluate(
        self,
        request: ActionRequest,
        task_authorization: TaskAuthorization | None = None,
        *,
        trusted_edit_risk_flags: tuple[str, ...] = (),
    ) -> PolicyDecision:
        classified = classify_action(request, self.config.workspace_root, self.config.mcp_risks)
        explicit_edit_approval = requires_explicit_edit_plan_approval(
            trusted_edit_risk_flags
        )
        if explicit_edit_approval and request.name.casefold() != "apply_workspace_edit_plan_v1":
            raise ValueError("trusted edit risks require the edit-plan apply tool")
        boundary = self._boundary_decision(classified)
        if boundary is not None:
            return boundary

        mode = self.config.approval_mode
        outside = Capability.OUTSIDE_WORKSPACE in classified.capabilities
        network = Capability.NETWORK in classified.capabilities
        trusted_workspace = self._trusted_workspace_action(
            classified, task_authorization
        )

        explicit = self._explicit_edit_decision(
            classified, explicit_edit_approval, trusted_workspace, mode
        )
        if explicit is not None:
            return explicit

        if mode is ApprovalMode.UNRESTRICTED:
            return self._decision(
                DecisionOutcome.ALLOW,
                classified,
                "allowed by unrestricted mode without approval",
            )

        return self._mode_decision(
            classified, trusted_workspace, outside, network
        )

    def _boundary_decision(
        self, classified: ActionClassification
    ) -> PolicyDecision | None:
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
                DecisionOutcome.ASK, classified, "approval required for a protected path"
            )
        return None

    def _explicit_edit_decision(
        self,
        classified: ActionClassification,
        required: bool,
        trusted_workspace: bool,
        mode: ApprovalMode,
    ) -> PolicyDecision | None:
        if not required or mode is ApprovalMode.PLAN or trusted_workspace:
            return None
        explicit = ActionClassification(
            classified.capabilities | {Capability.EXPLICIT_APPROVAL},
            RiskLevel.HIGH,
            "trusted edit plan includes protected existing-file or destructive risk",
        )
        return self._decision(
            DecisionOutcome.ASK,
            explicit,
            "explicit user approval required for the edit plan",
        )

    def _mode_decision(
        self,
        classified: ActionClassification,
        trusted_workspace: bool,
        outside: bool,
        network: bool,
    ) -> PolicyDecision:
        read_only = (
            classified.capabilities - frozenset({Capability.OUTSIDE_WORKSPACE})
            == frozenset({Capability.READ})
        )
        mode = self.config.approval_mode
        if mode is ApprovalMode.PLAN:
            outcome = DecisionOutcome.ALLOW if read_only and not outside else DecisionOutcome.DENY
            reason = (
                "allowed because plan mode permits read-only actions"
                if read_only and not outside
                else "denied because plan mode permits read-only actions only"
            )
            return self._decision(outcome, classified, reason)

        if trusted_workspace:
            return self._decision(
                DecisionOutcome.ALLOW,
                classified,
                "allowed by trusted current-workspace authorization",
            )

        if mode is ApprovalMode.ASK:
            return self._ask_decision(classified, read_only, outside)

        if mode is ApprovalMode.FULL_LOCAL:
            if outside or network:
                return self._decision(
                    DecisionOutcome.ASK,
                    classified,
                    "approval required at the local workspace boundary",
                )
            return self._decision(
                DecisionOutcome.ALLOW,
                classified,
                "allowed by full-local mode for a non-critical typed action",
            )

        return self._auto_decision(classified, read_only, outside, network)

    def _ask_decision(
        self, classified: ActionClassification, read_only: bool, outside: bool
    ) -> PolicyDecision:
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

    def _auto_decision(
        self,
        classified: ActionClassification,
        read_only: bool,
        outside: bool,
        network: bool,
    ) -> PolicyDecision:
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
            network and not self.config.allow_network
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

    def _trusted_workspace_action(
        self,
        classified: ActionClassification,
        task_authorization: TaskAuthorization | None,
    ) -> bool:
        if self.config.workspace_root is None or classified.capabilities.intersection(
            {Capability.NETWORK, Capability.OUTSIDE_WORKSPACE, Capability.PROTECTED_PATH}
        ):
            return False
        if task_authorization is None:
            return self.config.approval_mode in {
                ApprovalMode.AUTO,
                ApprovalMode.FULL_LOCAL,
            }
        authorized_root = Path(task_authorization.workspace_root).resolve(strict=False)
        if self.config.workspace_root != authorized_root:
            return False
        if Capability.EXECUTE in classified.capabilities:
            return task_authorization.allow_local_execute
        if Capability.WRITE in classified.capabilities:
            return task_authorization.allow_workspace_write
        return Capability.READ in classified.capabilities
