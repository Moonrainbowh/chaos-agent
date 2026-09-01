from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from code_agent.runtime.models import CommandResult, StreamName, TerminationReason
from code_agent.runtime.output_codec import DecodedOutput, decode_output

from .evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord
from .models import VerificationCommand, VerificationKind, VerificationUnavailable


_TEMP_PATH = re.compile(
    r'"(?:[A-Za-z]:\\(?:[^"\\\r\n]+\\)*(?:Temp|tmp)'
    r'(?:\\[^"\r\n]*)?|/tmp(?:/[^"\r\n]*)?)"'
    r'|(?:[A-Za-z]:\\(?:[^\s\\\r\n]+\\)*(?:Temp|tmp)'
    r'(?:\\[^\s\r\n]*)?|/tmp(?:/[^\s\r\n]*)?)(?=$|\s)',
    re.IGNORECASE,
)
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


def failure_fingerprint(
    stdout: bytes, stderr: bytes, returncode: int | None
) -> str:
    """Normalize stable text while retaining every undecodable source byte."""
    stdout_bytes = _normalized_bytes(stdout)
    stderr_bytes = _normalized_bytes(stderr)
    framed = b"".join(
        (
            str(returncode).encode("ascii"),
            len(stdout_bytes).to_bytes(8, "big"),
            stdout_bytes,
            len(stderr_bytes).to_bytes(8, "big"),
            stderr_bytes,
        )
    )
    return hashlib.sha256(framed).hexdigest()


def normalize_verification_result(identifier: str, criterion_id: str, command: VerificationCommand | VerificationUnavailable, result: CommandResult | None, generation: int, subject_hash: str) -> VerificationRunResult:
    if isinstance(command, VerificationUnavailable):
        evidence = EvidenceRecord.from_output(identifier, criterion_id, EvidenceOutcome.UNAVAILABLE, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, "", command.reason)
        return VerificationRunResult(evidence, None)
    if not isinstance(command, VerificationCommand):
        raise TypeError("command must be a verification command or unavailable")
    if result is None:
        evidence = EvidenceRecord.from_output(identifier, criterion_id, EvidenceOutcome.ERROR, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, "", "verifier returned no result")
        return VerificationRunResult(evidence, None)
    stdout = decode_output(
        result.stdout, truncated=StreamName.STDOUT in result.truncated_streams
    )
    stderr = decode_output(
        result.stderr, truncated=StreamName.STDERR in result.truncated_streams
    )
    output = f"stdout:\n{_render_output(stdout)}\nstderr:\n{_render_output(stderr)}"
    passed = result.reason is TerminationReason.EXITED and result.returncode == 0
    outcome = EvidenceOutcome.PASS if passed else EvidenceOutcome.FAIL
    diagnostic = "verification passed" if passed else output[:2_000] or f"verification ended: {result.reason.value}"
    evidence = EvidenceRecord.from_output(identifier, criterion_id, outcome, EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, output, diagnostic)
    repair = None if passed else RepairDirective(
        failure_fingerprint(result.stdout, result.stderr, result.returncode),
        evidence.diagnostic,
    )
    return VerificationRunResult(evidence, repair)


def _normalized_bytes(value: bytes) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("verification output must be bytes")
    text = value.decode("utf-8", errors="surrogateescape")
    normalized = _TIMESTAMP.sub("<time>", _TEMP_PATH.sub("<temp>", text))
    return normalized.encode("utf-8", errors="surrogateescape")


def _render_output(value: DecodedOutput) -> str:
    if value.text is not None:
        return value.text
    return (
        f"[decoding={value.status.value};encoding={value.encoding.value};"
        f"base64={value.base64_data}]"
    )


def no_progress(previous: VerificationRunResult, current: VerificationRunResult) -> bool:
    return previous.evidence.subject_hash == current.evidence.subject_hash and previous.repair is not None and current.repair is not None and previous.repair.failure_fingerprint == current.repair.failure_fingerprint
