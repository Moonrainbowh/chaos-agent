from __future__ import annotations

from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.policy.command_rules import ProcessRuleMatch, ProcessRuleStore
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import DecisionOutcome

from code_agent_win.action_support import error_result
from code_agent_win.edit_plan_dispatch import EditPlanAuthorization


def match_process_rule(
    store: ProcessRuleStore | None,
    request: ActionRequest,
    *,
    permission_root: Path,
    permission_fingerprint: str,
    execution_root: Path,
) -> ProcessRuleMatch | None:
    if store is None:
        return None
    return store.match(
        request,
        workspace_root=permission_root,
        workspace_fingerprint=permission_fingerprint,
        execution_root=execution_root,
    )


async def authorize_action(
    policy: ActionPolicy,
    approvals: ApprovalBroker,
    interactive: bool,
    request: ActionRequest,
    translated: ActionRequest,
    cancellation: CancellationToken,
    task_authorization: TaskAuthorization | None,
    edit_authorization: EditPlanAuthorization,
    process_rule: ProcessRuleMatch | None,
) -> ActionResult | None:
    def evaluate(item: ActionRequest):
        risks = (
            edit_authorization.risk_flags
            if item.name == "apply_workspace_edit_plan_v1"
            else ()
        )
        if risks:
            return policy.evaluate(
                item, task_authorization, trusted_edit_risk_flags=risks
            )
        if process_rule is not None and item is translated:
            return policy.evaluate(
                item,
                task_authorization,
                permanent_process_rule=process_rule.rule_id,
            )
        return policy.evaluate(item, task_authorization)

    decisions = (evaluate(request),)
    if translated is not request:
        decisions += (evaluate(translated),)
    denied = next(
        (item for item in decisions if item.outcome is DecisionOutcome.DENY), None
    )
    if denied is not None:
        return error_result(request, "action denied", denied.reason)
    asking = next(
        (item for item in decisions if item.outcome is DecisionOutcome.ASK), None
    )
    if asking is None:
        return None
    if not interactive:
        return error_result(
            request,
            "permission not granted; change access settings or add an exact command rule",
            error_code="approval_required",
        )
    approved = await approvals.request(
        ApprovalRequest(
            request.id,
            request.name,
            dict(request.arguments),
            asking.risk.value,
            translated.name,
            asking.reason,
            edit_authorization.view,
        ),
        cancellation,
    )
    return None if approved else error_result(request, "action denied by user")


def bind_process_rule(
    request: ActionRequest, process_rule: ProcessRuleMatch | None
) -> ActionRequest:
    if process_rule is None:
        return request
    return ActionRequest(
        request.id,
        request.name,
        {**request.arguments, "program": process_rule.program_path},
    )


def attach_permission_metadata(
    result: ActionResult,
    permission_source: str | None,
    process_rule: ProcessRuleMatch | None,
) -> ActionResult:
    metadata: dict[str, object] = {}
    if permission_source is not None:
        metadata["permission_source"] = permission_source
    if process_rule is not None:
        metadata.update(
            permission_source="permanent_rule",
            permission_rule_id=process_rule.rule_id,
        )
    if not metadata:
        return result
    return ActionResult(
        result.request_id,
        result.name,
        result.output,
        result.is_error,
        {**result.metadata, **metadata},
    )


__all__ = (
    "attach_permission_metadata",
    "authorize_action",
    "bind_process_rule",
    "match_process_rule",
)
