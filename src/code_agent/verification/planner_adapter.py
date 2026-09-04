from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import VerificationKind, VerificationRequest
from .syntax_check import SyntaxCheckResult, check_syntax

if TYPE_CHECKING:
    from .planner import VerificationPlan, VerificationPlanner


@dataclass(frozen=True)
class PlanExecutionOutcome:
    plan: VerificationPlan
    syntax_passed: bool
    syntax_errors: tuple[SyntaxCheckResult, ...] = ()
    tests_skipped: bool = False
    request: VerificationRequest | None = None
    diagnostic: str = ""


class PlannerVerificationAdapter:
    """Bridge a VerificationPlan into a request or fast-check outcome."""

    def __init__(self, planner: VerificationPlanner) -> None:
        self.planner = planner

    def execute_syntax_checks(
        self, plan: VerificationPlan, file_contents: dict[str, str] | None = None
    ) -> tuple[bool, tuple[SyntaxCheckResult, ...]]:
        errors: list[SyntaxCheckResult] = []
        for target in plan.syntax_targets:
            text = file_contents.get(target) if file_contents else None
            result = check_syntax(
                target, text, workspace_root=self.planner._root
            )
            if not result.is_valid:
                errors.append(result)
        return not errors, tuple(errors)

    def prepare_request(
        self,
        plan: VerificationPlan,
        default_kind: VerificationKind = VerificationKind.PYTHON_UNITTEST,
    ) -> PlanExecutionOutcome:
        syntax_ok, errors = self.execute_syntax_checks(plan)
        if not syntax_ok:
            diagnostic = "; ".join(
                error.format_diagnostic() for error in errors
            )
            return PlanExecutionOutcome(
                plan, False, errors, True, None,
                f"Syntax check failed: {diagnostic}",
            )
        if plan.skip_tests:
            return PlanExecutionOutcome(
                plan, True, (), True, None,
                f"Tests bypassed safely ({plan.tier.value} risk): {plan.reason}",
            )
        targets = () if plan.require_full_gate else plan.targeted_tests
        request = VerificationRequest(kind=default_kind, targets=targets)
        return PlanExecutionOutcome(
            plan, True, (), False, request,
            f"Executing targeted verification for {plan.tier.value} risk tier",
        )
