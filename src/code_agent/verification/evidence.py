from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class EvidenceOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED_BY_USER = "skipped_by_user"
    USER_CONFIRMED = "user_confirmed"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    UNSTABLE = "unstable"
    INTERRUPTED = "interrupted"


class EvidenceProvenance(str, Enum):
    SYSTEM_VERIFIER = "system_verifier"
    SYSTEM_PLANNER = "system_planner"
    USER_COMMAND = "user_command"
    USER_CONFIRMATION = "user_confirmation"


def _text(value: object, name: str, maximum: int = 1_024) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded non-blank text")
    return value


def bounded_diagnostic(value: str, maximum: int = 2_000) -> str:
    text = _text(value, "diagnostic", maximum)
    lowered = text.casefold()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "authorization:")):
        return "[redacted diagnostic]"
    return "".join(character if character >= " " or character in "\r\n\t" else "?" for character in text)


@dataclass(frozen=True)
class EvidenceRecord:
    identifier: str
    criterion_id: str
    outcome: EvidenceOutcome
    provenance: EvidenceProvenance
    generation: int
    subject_hash: str
    output_hash: str
    diagnostic: str
    manual_only: bool = False

    def __post_init__(self) -> None:
        for name, maximum in (("identifier", 128), ("criterion_id", 128), ("subject_hash", 128), ("output_hash", 128)):
            object.__setattr__(self, name, _text(getattr(self, name), name, maximum))
        if not isinstance(self.outcome, EvidenceOutcome) or not isinstance(self.provenance, EvidenceProvenance):
            raise TypeError("evidence outcome and provenance must be typed")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("generation must be non-negative")
        if not isinstance(self.manual_only, bool):
            raise TypeError("manual_only must be boolean")
        if self.outcome is EvidenceOutcome.USER_CONFIRMED and (self.provenance is not EvidenceProvenance.USER_CONFIRMATION or not self.manual_only):
            raise ValueError("user confirmation is only valid for manual criteria")
        if self.provenance in {
            EvidenceProvenance.SYSTEM_VERIFIER,
            EvidenceProvenance.SYSTEM_PLANNER,
        } and self.outcome is EvidenceOutcome.USER_CONFIRMED:
            raise ValueError("system evidence cannot emit user confirmation")
        object.__setattr__(self, "diagnostic", bounded_diagnostic(self.diagnostic))

    @classmethod
    def from_output(cls, identifier: str, criterion_id: str, outcome: EvidenceOutcome, provenance: EvidenceProvenance, generation: int, subject_hash: str, output: str, diagnostic: str, *, manual_only: bool = False) -> "EvidenceRecord":
        return cls(identifier, criterion_id, outcome, provenance, generation, subject_hash, hashlib.sha256(output.encode("utf-8")).hexdigest(), diagnostic, manual_only)

    def to_dict(self) -> dict[str, object]:
        return {"identifier": self.identifier, "criterion_id": self.criterion_id, "outcome": self.outcome.value, "provenance": self.provenance.value, "generation": self.generation, "subject_hash": self.subject_hash, "output_hash": self.output_hash, "diagnostic": self.diagnostic, "manual_only": self.manual_only}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceRecord":
        return cls(str(value["identifier"]), str(value["criterion_id"]), EvidenceOutcome(str(value["outcome"])), EvidenceProvenance(str(value["provenance"])), int(value["generation"]), str(value["subject_hash"]), str(value["output_hash"]), str(value["diagnostic"]), bool(value.get("manual_only", False)))


def append_evidence(existing: Sequence[EvidenceRecord], record: EvidenceRecord) -> tuple[EvidenceRecord, ...]:
    items = tuple(existing)
    if not all(isinstance(item, EvidenceRecord) for item in items) or not isinstance(record, EvidenceRecord):
        raise TypeError("evidence ledger must be typed")
    if any(item.identifier == record.identifier for item in items):
        raise ValueError("evidence identifier is append-only and unique")
    return items + (record,)


def evidence_satisfies_required(record: EvidenceRecord) -> bool:
    trusted_pass = record.outcome is EvidenceOutcome.PASS and record.provenance in {
        EvidenceProvenance.SYSTEM_VERIFIER,
        EvidenceProvenance.SYSTEM_PLANNER,
    }
    user_pass = (
        record.outcome is EvidenceOutcome.USER_CONFIRMED
        and record.provenance is EvidenceProvenance.USER_CONFIRMATION
        and record.manual_only
    )
    return trusted_pass or user_pass
