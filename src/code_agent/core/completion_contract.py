from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class TaskIntent(str, Enum):
    ANALYZE = "analyze"
    MODIFY = "modify"


class CriterionRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class CriterionStrength(str, Enum):
    USER = "user"
    SELF_AUTHORED = "self_authored"
    INTEGRITY = "integrity"


class CompletionKind(str, Enum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"


def _text(value: object, name: str, maximum: int = 1_024) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded non-blank text")
    return value


@dataclass(frozen=True)
class AcceptanceCriterion:
    identifier: str
    description: str
    requirement: CriterionRequirement
    strength: CriterionStrength

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _text(self.identifier, "identifier", 128))
        object.__setattr__(self, "description", _text(self.description, "description", 2_000))
        if not isinstance(self.requirement, CriterionRequirement) or not isinstance(self.strength, CriterionStrength):
            raise TypeError("criterion requirement and strength must be typed")
        if self.strength is CriterionStrength.INTEGRITY and self.requirement is not CriterionRequirement.REQUIRED:
            raise ValueError("integrity criteria must be required")


@dataclass(frozen=True)
class TaskContractRevision:
    revision: int
    intent: TaskIntent
    criteria: tuple[AcceptanceCriterion, ...]

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if not isinstance(self.intent, TaskIntent):
            raise TypeError("intent must be a TaskIntent")
        criteria = tuple(self.criteria)
        if not criteria or len(criteria) > 32 or not all(isinstance(item, AcceptanceCriterion) for item in criteria):
            raise ValueError("criteria must contain 1 to 32 typed criteria")
        if len({item.identifier for item in criteria}) != len(criteria):
            raise ValueError("criterion identifiers must be unique")
        object.__setattr__(self, "criteria", criteria)

    def revise(self, criteria: Sequence[AcceptanceCriterion]) -> "TaskContractRevision":
        revised = TaskContractRevision(self.revision + 1, self.intent, tuple(criteria))
        previous_user_required = {item.identifier for item in self.criteria if item.strength is CriterionStrength.USER and item.requirement is CriterionRequirement.REQUIRED}
        next_by_id = {item.identifier: item for item in revised.criteria}
        if any(identifier not in next_by_id or next_by_id[identifier].requirement is not CriterionRequirement.REQUIRED for identifier in previous_user_required):
            raise ValueError("user-required criteria cannot be downgraded or removed")
        return revised


@dataclass(frozen=True)
class ActionEffect:
    generation: int
    changed_paths: tuple[str, ...]
    content_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("generation must be positive")
        paths, hashes = tuple(self.changed_paths), tuple(self.content_hashes)
        if not paths or len(paths) != len(hashes) or not all(isinstance(item, str) and item for item in paths + hashes):
            raise ValueError("action effects need matching non-blank paths and hashes")
        object.__setattr__(self, "changed_paths", paths)
        object.__setattr__(self, "content_hashes", hashes)


@dataclass(frozen=True)
class CompletionCandidate:
    criterion_id: str
    passed: bool
    generation: int
    subject_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _text(self.criterion_id, "criterion_id", 128))
        if not isinstance(self.passed, bool) or isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("completion candidate has invalid values")
        object.__setattr__(self, "subject_hash", _text(self.subject_hash, "subject_hash", 128))


@dataclass(frozen=True)
class CompletionAssessment:
    kind: CompletionKind
    unmet_required: tuple[str, ...]


def assess_completion(contract: TaskContractRevision, generation: int, subject_hash: str, candidates: Sequence[CompletionCandidate]) -> CompletionAssessment:
    if not isinstance(contract, TaskContractRevision) or isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise TypeError("contract and generation must be valid")
    subject_hash = _text(subject_hash, "subject_hash", 128)
    latest = {candidate.criterion_id: candidate for candidate in candidates if isinstance(candidate, CompletionCandidate) and candidate.generation == generation and candidate.subject_hash == subject_hash}
    unmet = tuple(item.identifier for item in contract.criteria if item.requirement is CriterionRequirement.REQUIRED and not (item.identifier in latest and latest[item.identifier].passed))
    if not unmet:
        return CompletionAssessment(CompletionKind.VERIFIED, ())
    any_passed = any(candidate.passed for candidate in latest.values())
    return CompletionAssessment(CompletionKind.PARTIAL if any_passed else CompletionKind.UNVERIFIED, unmet)


class VerificationService(Protocol):
    async def assess(self, contract: TaskContractRevision, generation: int, subject_hash: str) -> Sequence[CompletionCandidate]: ...
