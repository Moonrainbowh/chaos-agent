from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from code_agent.runtime.models import CommandResult, TerminationReason

from .evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord
from .models import VerificationCommand, VerificationKind, VerificationUnavailable


_TEMP_PATH = re.compile(r"[A-Za-z]:\\[^\s\r\n]*?(?:Temp|tmp)[^\s\r\n]*|/tmp/[^\s\r\n]*", re.IGNORECASE)
_TIMESTAMP = re.compile(r"\b\d{4}-\d\d-\d\d[T ][0-9:.+-]+\b")


@dataclass(frozen=True)
class VerifierDescriptor:
    kind: VerificationKind
    ecosystem: str
    scope: str
    timeout_s: int
    estimated_cost: str


@dataclass(frozen=True)
class RepairDirective:
    failure_fingerprint: str
    diagnostic: str


@dataclass(frozen=True)
class VerificationRunResult:
    evidence: EvidenceRecord
    repair: RepairDirective | None


def failure_fingerprint(stdout: str, stderr: str, returncode: int | None) -> str:
    normalized = _TIMESTAMP.sub("<time>", _TEMP_PATH.sub("<temp>", f"{stdout}\n{stderr}"))
    return hashlib.sha256(f"{returncode}|{normalized[:4_000]}".encode("utf-8")).hexdigest()


def normalize_verification_result(identifier: str, criterion_id: str, command: VerificationCommand | VerificationUnavailable, result: CommandResult | None, generation: int, subject_hash: str) -> VerificationRunResult:
    if isinstance(command, VerificationUnavailable):
        evidence = EvidenceRecord.from_output(identifier, criterion_id, EvidenceOutcome.UNAVAILABLE, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, "", command.reason)
        return VerificationRunResult(evidence, None)
    if not isinstance(command, VerificationCommand):
        raise TypeError("command must be a verification command or unavailable")
    if result is None:
        evidence = EvidenceRecord.from_output(identifier, criterion_id, EvidenceOutcome.ERROR, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, "", "verifier returned no result")
        return VerificationRunResult(evidence, None)
    output = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace")
    passed = result.reason is TerminationReason.EXITED and result.returncode == 0
    outcome = EvidenceOutcome.PASS if passed else EvidenceOutcome.FAIL
    diagnostic = "verification passed" if passed else output[:2_000] or f"verification ended: {result.reason.value}"
    evidence = EvidenceRecord.from_output(identifier, criterion_id, outcome, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, output, diagnostic)
    repair = None if passed else RepairDirective(failure_fingerprint(result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"), result.returncode), evidence.diagnostic)
    return VerificationRunResult(evidence, repair)


def no_progress(previous: VerificationRunResult, current: VerificationRunResult) -> bool:
    return previous.evidence.subject_hash == current.evidence.subject_hash and previous.repair is not None and current.repair is not None and previous.repair.failure_fingerprint == current.repair.failure_fingerprint
