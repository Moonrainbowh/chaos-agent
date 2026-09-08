from __future__ import annotations

import uuid

from code_agent.core.models import ToolCall

from .planner import RiskTier, VerificationPhase, VerificationPlan
from .task_evidence import (
    FINAL_TESTS_CRITERION,
    INTEGRITY_CRITERION,
    RISK_VALIDATION_CRITERION,
)


class PlannedCallRegistry:
    """Bind Host-created verifier calls to their unforgeable completion scope."""

    def __init__(self) -> None:
        self._calls: dict[
            str, tuple[str, VerificationPhase, RiskTier, str]
        ] = {}

    def create(
        self,
        task_id: str,
        plan: VerificationPlan,
        kind: str,
        cwd: str,
        targets: tuple[str, ...],
        step: str,
    ) -> ToolCall:
        identifier = f"system-{plan.phase.value}-{uuid.uuid4().hex}"
        self._calls[identifier] = (task_id, plan.phase, plan.tier, step)
        arguments: dict[str, object] = {"kind": kind, "cwd": cwd}
        if targets:
            arguments["targets"] = list(targets)
        return ToolCall(identifier, "run_verification", arguments)

    def consume_criterion(self, task_id: str, request_id: str) -> str:
        planned = self._calls.pop(request_id, None)
        if planned is None or planned[0] != task_id:
            return INTEGRITY_CRITERION
        _, phase, tier, step = planned
        if phase is VerificationPhase.LOCAL_MILESTONE:
            return (
                RISK_VALIDATION_CRITERION
                if tier is RiskTier.MEDIUM
                else INTEGRITY_CRITERION
            )
        if tier is RiskTier.CRITICAL and step == "tests":
            return FINAL_TESTS_CRITERION
        return RISK_VALIDATION_CRITERION
