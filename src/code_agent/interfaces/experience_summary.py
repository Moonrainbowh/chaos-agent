"""User-facing task facts derived from trusted local projections.

This module deliberately does not infer completion from model text.  It turns
the existing terminal state, diff and evidence projections into a compact
summary that a CLI/TUI can render without exposing internal event names.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from code_agent.interfaces.diff_view import DiffView
from code_agent.verification.evidence import EvidenceOutcome, EvidenceRecord


@dataclass(frozen=True)
class ArtifactRef:
    """A bounded, user-openable result produced by a task."""

    label: str
    locator: str
    detail: str = ""

    def __post_init__(self) -> None:
        for name, value, limit in (("label", self.label, 120), ("locator", self.locator, 512), ("detail", self.detail, 240)):
            if not isinstance(value, str) or len(value) > limit or (name != "detail" and not value.strip()):
                raise ValueError(f"artifact {name} is invalid")


@dataclass(frozen=True)
class DiffFacts:
    files: int = 0
    additions: int = 0
    removals: int = 0
    sensitive_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationFacts:
    total: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0


@dataclass(frozen=True)
class ExperienceSnapshot:
    """Stable facts suitable for a compact status panel or JSON response."""

    status: str
    label: str
    changed: DiffFacts
    verification: VerificationFacts
    artifacts: tuple[ArtifactRef, ...] = ()
    error: str | None = None
    next_action: str = ""

    @property
    def can_claim_completion(self) -> bool:
        return self.status == "completed" and self.verification.failed == 0 and self.verification.unknown == 0 and (
            self.changed.files == 0 or self.verification.passed > 0
        )


_LABELS = {
    "idle": "Ready", "running": "Working", "building_context": "Analyzing",
    "waiting_model": "Generating", "streaming_response": "Writing",
    "preparing_action": "Preparing change", "verifying": "Verifying",
    "waiting_decision": "Waiting for your decision", "paused": "Paused",
    "completed": "Completed", "accepted_partial": "Partially delivered",
    "failed": "Failed", "error": "Failed", "cancelled": "Stopped",
    "interrupted": "Interrupted",
}
_SENSITIVE_MARKERS = (".env", ".pem", ".key", "secret", "credential", "token")


def build_experience_snapshot(
    state: Any,
    *,
    evidence: Sequence[EvidenceRecord] = (),
    artifacts: Sequence[ArtifactRef] = (),
    diff: str | None = None,
    error: BaseException | str | None = None,
) -> ExperienceSnapshot:
    """Build a truthful summary from trusted projections only."""
    raw_status = str(getattr(state, "task_status", None) or getattr(state, "status", "idle"))
    changed = _diff_facts(diff if diff is not None else getattr(state, "diff", None))
    verification = _verification_facts(evidence)
    status = _effective_status(raw_status, changed, verification, error)
    message = _error_text(error)
    return ExperienceSnapshot(
        status=status,
        label=_LABELS.get(status, status.replace("_", " ").title()),
        changed=changed,
        verification=verification,
        artifacts=tuple(artifacts),
        error=message,
        next_action=_next_action(status, changed, verification, message),
    )


def format_experience_summary(snapshot: ExperienceSnapshot) -> str:
    """Render a short, actionable summary; large artifacts stay behind locators."""
    lines = [snapshot.label]
    if snapshot.changed.files:
        lines.append(f"Changed: {snapshot.changed.files} files (+{snapshot.changed.additions} -{snapshot.changed.removals})")
        if snapshot.changed.sensitive_paths:
            lines.append("Review sensitive paths: " + ", ".join(snapshot.changed.sensitive_paths))
    if snapshot.verification.total:
        lines.append(f"Verification: {snapshot.verification.passed} passed, {snapshot.verification.failed} failed")
    elif snapshot.changed.files:
        lines.append("Verification: not run")
    if snapshot.error:
        lines.append("Error: " + snapshot.error)
    if snapshot.artifacts:
        lines.append("Artifacts: " + ", ".join(item.label for item in snapshot.artifacts))
    if snapshot.next_action:
        lines.append("Next: " + snapshot.next_action)
    return "\n".join(lines)


def _diff_facts(unified: str | None) -> DiffFacts:
    if not isinstance(unified, str) or not unified.strip():
        return DiffFacts()
    view = DiffView.parse(unified)
    paths = tuple(file.path for file in view.files)
    sensitive = tuple(path for path in paths if any(marker in path.casefold() for marker in _SENSITIVE_MARKERS))
    return DiffFacts(len(paths), sum(file.additions for file in view.files), sum(file.removals for file in view.files), sensitive)


def _verification_facts(records: Sequence[EvidenceRecord]) -> VerificationFacts:
    typed = tuple(item for item in records if isinstance(item, EvidenceRecord))
    passed = sum(item.outcome is EvidenceOutcome.PASS for item in typed)
    failed = sum(item.outcome in {EvidenceOutcome.FAIL, EvidenceOutcome.ERROR} for item in typed)
    return VerificationFacts(len(typed), passed, failed, len(typed) - passed - failed)


def _effective_status(raw: str, changed: DiffFacts, verification: VerificationFacts, error: BaseException | str | None) -> str:
    if error is not None or raw in {"error", "failed"}:
        return "failed" if not changed.files else "accepted_partial"
    if raw == "completed" and changed.files and verification.total == 0:
        return "verifying"
    if raw == "completed" and verification.failed:
        return "verifying"
    return raw


def _error_text(error: BaseException | str | None) -> str | None:
    if error is None:
        return None
    text = " ".join(str(error).split())[:240]
    return text or type(error).__name__ if isinstance(error, BaseException) else text or "Unknown error"


def _next_action(status: str, changed: DiffFacts, verification: VerificationFacts, error: str | None) -> str:
    if status == "verifying":
        return "Run or inspect verification before calling this complete."
    if status == "waiting_decision":
        return "Choose whether to approve, revise, or stop."
    if status == "accepted_partial":
        return "Review the retained changes in the current workspace and decide whether to continue."
    if status == "failed":
        return "Inspect the error and retry from the current workspace." if error else "Inspect the failed step."
    if status == "completed":
        return "Review the change summary." if changed.files else "No further action required."
    return "Wait for the current step to finish."
