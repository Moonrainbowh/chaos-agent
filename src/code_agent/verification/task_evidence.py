from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from code_agent.core.models import ActionResult
from code_agent.core.task_state import TaskState
from code_agent.core.completion_contract import (
    AcceptanceCriterion,
    CriterionRequirement,
    CriterionStrength,
    TaskContractRevision,
    TaskIntent,
)
from code_agent.core.verification_state import VerifierOutcome
from code_agent.projects.discovery import ProjectKind
from .planner import VerificationPlan

from .evidence import (
    EvidenceOutcome,
    EvidenceProvenance,
    EvidenceRecord,
    evidence_satisfies_required,
)


RISK_VALIDATION_CRITERION = "risk-appropriate-validation"
FINAL_TESTS_CRITERION = "final-project-tests"
INTEGRITY_CRITERION = "integrity"
LEGACY_PROJECT_TESTS_CRITERION = "project-tests"
_DOCUMENT_NAMES = {
    "readme", "readme.md", "license", "license.md", "license.txt",
    "changelog.md", "contributing.md",
}


def validation_contract(revision: int, intent: TaskIntent) -> TaskContractRevision:
    return TaskContractRevision(
        revision,
        intent,
        (
            AcceptanceCriterion(
                RISK_VALIDATION_CRITERION,
                "Host-planned validation satisfies the current change risk tier.",
                CriterionRequirement.REQUIRED,
                CriterionStrength.INTEGRITY,
            ),
        ),
    )


def is_legacy_default_contract(contract: TaskContractRevision) -> bool:
    return (
        len(contract.criteria) == 1
        and contract.criteria[0].identifier == LEGACY_PROJECT_TESTS_CRITERION
        and contract.criteria[0].strength is CriterionStrength.INTEGRITY
    )


def planner_attestation_allowed(changed_files: Sequence[str]) -> bool:
    """Allow no-test proof only for paths that are unambiguously documentation."""
    if not changed_files:
        return False
    for raw in changed_files:
        path = PurePosixPath(raw.replace("\\", "/"))
        parts = {part.casefold() for part in path.parts[:-1]}
        if path.name.casefold() in _DOCUMENT_NAMES:
            continue
        if parts.intersection({"docs", "doc", "documentation"}):
            continue
        return False
    return True


def evidence_from_result(
    identifier: str,
    result: ActionResult,
    generation: int,
    subject_hash: str,
    criterion_id: str,
) -> EvidenceRecord:
    output = result.output if isinstance(result.output, Mapping) else {}
    diagnostic = str(
        output.get("stderr")
        or output.get("detail")
        or output.get("error")
        or "verification passed"
    )
    if not result.is_error:
        outcome = EvidenceOutcome.PASS
    elif output.get("error") == "verification unavailable":
        outcome = EvidenceOutcome.UNAVAILABLE
    else:
        outcome = EvidenceOutcome.FAIL
    return EvidenceRecord.from_output(
        identifier,
        criterion_id,
        outcome,
        EvidenceProvenance.SYSTEM_VERIFIER,
        generation,
        subject_hash,
        repr(dict(output)),
        diagnostic,
    )


def planner_attestation(
    generation: int, subject_hash: str, payload: str, diagnostic: str
) -> EvidenceRecord:
    return EvidenceRecord.from_output(
        f"planner-{uuid.uuid4().hex}",
        RISK_VALIDATION_CRITERION,
        EvidenceOutcome.PASS,
        EvidenceProvenance.SYSTEM_PLANNER,
        generation,
        subject_hash,
        payload,
        diagnostic,
    )


async def record_planner_attestation(
    sessions: object,
    task_id: str,
    state: TaskState,
    plan: VerificationPlan,
) -> None:
    existing = tuple(
        item for item in await sessions.list_verification_evidence(task_id)
        if isinstance(item, EvidenceRecord)
        and item.generation == state.code_generation
        and item.subject_hash == state.subject_hash
    )
    if has_passing(existing, RISK_VALIDATION_CRITERION):
        return
    run_id = uuid.uuid4().hex
    await sessions.begin_verification_run(
        run_id, task_id, state.code_generation, state.subject_hash
    )
    evidence = planner_attestation(
        state.code_generation,
        state.subject_hash,
        repr(plan.to_dict()),
        f"No executable files changed ({plan.reason})",
    )
    await sessions.append_verification_evidence(run_id, task_id, evidence)
    await sessions.close_verification_run(run_id, "completed")


def verifier_outcome(records: Sequence[EvidenceRecord]) -> VerifierOutcome:
    if any(item.outcome is EvidenceOutcome.UNAVAILABLE for item in records):
        return VerifierOutcome.UNAVAILABLE
    failed = {EvidenceOutcome.FAIL, EvidenceOutcome.ERROR, EvidenceOutcome.UNSTABLE}
    if any(item.outcome in failed for item in records):
        return VerifierOutcome.FAIL
    if any(evidence_satisfies_required(item) for item in records):
        return VerifierOutcome.PASS
    return VerifierOutcome.NOT_RUN


def has_passing(records: Sequence[EvidenceRecord], criterion: str) -> bool:
    return any(
        item.criterion_id == criterion
        and item.outcome is EvidenceOutcome.PASS
        and evidence_satisfies_required(item)
        for item in records
    )


def test_kind(kind: ProjectKind, recipes: Sequence[object]) -> str | None:
    available = {
        getattr(recipe, "label", None)
        for recipe in recipes
        if getattr(recipe, "available", False)
    }
    if kind is ProjectKind.PYTHON and "python unittest" in available:
        return "python_unittest"
    if kind is ProjectKind.NODE and "npm test" in available:
        return "node_test"
    if kind is ProjectKind.DOTNET and "dotnet test" in available:
        return "dotnet_test"
    return None


def build_kind(kind: ProjectKind) -> str:
    if kind is ProjectKind.PYTHON:
        return "python_build"
    if kind is ProjectKind.NODE:
        return "node_build"
    return "dotnet_build"
