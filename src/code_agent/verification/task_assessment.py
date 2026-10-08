"""Match completion proof to the current Host final plan, not arbitrary exit 0."""
from __future__ import annotations

from code_agent.projects.discovery import discover_projects
from .evidence import EvidenceProvenance, evidence_satisfies_current_verifier
from .planner import RiskTier, VerificationPhase
from .task_evidence import (
    FINAL_TESTS_CRITERION, RISK_VALIDATION_CRITERION, build_kind,
    build_verifier_identity_from_arguments, planner_attestation_allowed, test_kind,
)


def final_plan_proof(service, task_id, state, records):
    """Return evidence identifiers satisfying the one frozen risk criterion.

    The persisted criterion remains unchanged. Critical risk validation means
    both project tests and build; a passing targeted milestone cannot replace
    the final full gate. Unknown projects retain their missing-proof outcome.
    """
    records = tuple({(item.criterion_id, item.verifier_identity or item.provenance.value): item
                     for item in records}.values())
    if not state.files_changed:
        return {item.identifier for item in records}, ()
    plan = service.planner.plan(
        state.files_changed, phase=VerificationPhase.FINAL_GATE,
        diff="\n".join(service._task_diffs.get(task_id, ())),
    )
    if planner_attestation_allowed(plan):
        return {item.identifier for item in records
                if item.provenance is EvidenceProvenance.SYSTEM_PLANNER}, ()
    project = next(iter(discover_projects(service._guard.root, service._guard)), None)
    if project is None:
        return set(), ("No supported project verifier was discovered for the changed files.",)
    kind = test_kind(project.kind, project.recipes)
    if kind is None:
        reasons = tuple(recipe.reason for recipe in project.recipes
                        if not recipe.available and recipe.reason)
        return set(), reasons or ("Project test verifier is unavailable.",)
    targets = () if plan.require_full_gate else plan.targeted_tests
    criterion = FINAL_TESTS_CRITERION if plan.tier is RiskTier.CRITICAL else RISK_VALIDATION_CRITERION
    identity = build_verifier_identity_from_arguments(
        {"kind": kind, "cwd": project.root, "targets": targets}, criterion,
    )
    tests = tuple(item for item in records if item.criterion_id == criterion
                  and item.verifier_identity == identity
                  and evidence_satisfies_current_verifier(item))
    if plan.tier is not RiskTier.CRITICAL:
        return {item.identifier for item in tests}, ()
    build_identity = build_verifier_identity_from_arguments(
        {"kind": build_kind(project.kind), "cwd": project.root, "targets": ()},
        RISK_VALIDATION_CRITERION,
    )
    builds = tuple(item for item in records if item.criterion_id == RISK_VALIDATION_CRITERION
                   and item.verifier_identity == build_identity
                   and evidence_satisfies_current_verifier(item))
    return ({item.identifier for item in builds} if tests else set()), ()
