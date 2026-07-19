from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from ._rewind_candidate_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
)
from ._rewind_model_validation import (
    normalize_utc,
    validate_bool,
    validate_digest,
    validate_exact_tuple,
    validate_nonnegative_int,
    validate_path,
    validate_positive_int,
    validate_text,
    validate_unique_strings,
)


class RewindKind(str, Enum):
    CONVERSATION = "conversation"
    CODE = "code"
    BOTH = "both"


class RewindDisabledReason(str, Enum):
    CHECKPOINT_NOT_FOUND = "checkpoint-not-found"
    MESSAGE_BOUND_MISSING = "message-bound-missing"
    MESSAGE_BOUND_INVALID = "message-bound-invalid"
    CODE_COVERAGE_UNAVAILABLE = "code-coverage-unavailable"
    CODE_JOURNAL_INCOMPLETE = "code-journal-incomplete"
    PENDING_WORKSPACE_MUTATION = "pending-workspace-mutation"
    SNAPSHOT_MISSING = "snapshot-missing"
    SNAPSHOT_INVALID = "snapshot-invalid"
    WORKSPACE_CONFLICT = "workspace-conflict"
    PREVIEW_LIMIT_EXCEEDED = "preview-limit-exceeded"
    SOURCE_CHANGED_DURING_PREVIEW = "source-changed-during-preview"


_CONVERSATION_REASONS = frozenset(
    {
        RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        RewindDisabledReason.MESSAGE_BOUND_MISSING,
        RewindDisabledReason.MESSAGE_BOUND_INVALID,
        RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
    }
)
_CODE_REASONS = frozenset(
    {
        RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE,
        RewindDisabledReason.CODE_JOURNAL_INCOMPLETE,
        RewindDisabledReason.PENDING_WORKSPACE_MUTATION,
        RewindDisabledReason.SNAPSHOT_MISSING,
        RewindDisabledReason.SNAPSHOT_INVALID,
        RewindDisabledReason.WORKSPACE_CONFLICT,
        RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,
        RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
    }
)
_REASON_PRIORITY = {
    reason: priority for priority, reason in enumerate(RewindDisabledReason)
}


@dataclass(frozen=True)
class RewindAsOf:
    message_sequence: int | None
    event_sequence: int | None
    mutation_sequence: int | None
    coverage_generation: int | None
    relevant_path_digest: str | None
    captured_at: datetime

    def __post_init__(self) -> None:
        for field in ("message_sequence", "event_sequence", "mutation_sequence"):
            validate_nonnegative_int(getattr(self, field), field, optional=True)
        validate_positive_int(
            self.coverage_generation, "coverage_generation", optional=True
        )
        validate_digest(self.relevant_path_digest)
        object.__setattr__(
            self, "captured_at", normalize_utc(self.captured_at, "captured_at")
        )


@dataclass(frozen=True)
class RewindPath:
    path: str
    baseline_provenance: str
    preserves_pre_agent_baseline: bool

    def __post_init__(self) -> None:
        validate_path(self.path)
        validate_text(self.baseline_provenance, "baseline_provenance")
        validate_bool(
            self.preserves_pre_agent_baseline, "preserves_pre_agent_baseline"
        )


def _validate_paths(value: object) -> tuple[RewindPath, ...]:
    paths = validate_exact_tuple(value, "code_paths", RewindPath)
    validate_unique_strings(tuple(item.path for item in paths), "code path")
    return paths


def _validate_optional_reason(
    value: object,
    field: str,
    allowed: frozenset[RewindDisabledReason],
) -> RewindDisabledReason | None:
    if value is None:
        return None
    if type(value) is not RewindDisabledReason:
        raise TypeError(f"{field} must be a RewindDisabledReason or None")
    if value not in allowed:
        raise ValueError(f"{field} is not valid for its facet")
    return value


@dataclass(frozen=True)
class RewindFacts:
    checkpoint_id: str
    checkpoint_label: str
    checkpoint_created_at: datetime
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    conversation_disabled_reason: RewindDisabledReason | None
    code_disabled_reason: RewindDisabledReason | None

    def __post_init__(self) -> None:
        validate_text(self.checkpoint_id, "checkpoint_id")
        validate_text(self.checkpoint_label, "checkpoint_label")
        object.__setattr__(
            self,
            "checkpoint_created_at",
            normalize_utc(self.checkpoint_created_at, "checkpoint_created_at"),
        )
        if type(self.as_of) is not RewindAsOf:
            raise TypeError("as_of must be a RewindAsOf")
        validate_nonnegative_int(
            self.conversation_messages, "conversation_messages"
        )
        paths = _validate_paths(self.code_paths)
        conversation_reason = _validate_optional_reason(
            self.conversation_disabled_reason,
            "conversation_disabled_reason",
            _CONVERSATION_REASONS,
        )
        code_reason = _validate_optional_reason(
            self.code_disabled_reason, "code_disabled_reason", _CODE_REASONS
        )
        if conversation_reason is not None and self.conversation_messages != 0:
            raise ValueError("disabled conversation facet must be empty")
        if code_reason is not None and paths:
            raise ValueError("disabled code facet must be empty")
        source_changed = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
        if conversation_reason is source_changed and paths:
            raise ValueError("source change must clear code facts")
        if code_reason is source_changed and self.conversation_messages != 0:
            raise ValueError("source change must clear conversation facts")


def _validate_reasons(
    value: object, kind: RewindKind
) -> tuple[RewindDisabledReason, ...]:
    reasons = validate_exact_tuple(
        value, "disabled_reasons", RewindDisabledReason
    )
    if len(set(reasons)) != len(reasons):
        raise ValueError("disabled_reasons must not contain duplicates")
    priorities = tuple(_REASON_PRIORITY[reason] for reason in reasons)
    if priorities != tuple(sorted(priorities)):
        raise ValueError("disabled_reasons must follow enum priority")
    allowed = _allowed_reasons(kind)
    if any(reason not in allowed for reason in reasons):
        raise ValueError("disabled reason does not apply to kind")
    return reasons


def _allowed_reasons(kind: RewindKind) -> frozenset[RewindDisabledReason]:
    if kind is RewindKind.CONVERSATION:
        return _CONVERSATION_REASONS
    if kind is RewindKind.CODE:
        return _CODE_REASONS
    return _CONVERSATION_REASONS | _CODE_REASONS


def _validate_preview_flags(
    reasons: tuple[RewindDisabledReason, ...],
    enabled: object,
    requires_confirmation: object,
    apply_available: object,
    requires_git_reset: object,
) -> None:
    for field, value in (
        ("enabled", enabled),
        ("requires_confirmation", requires_confirmation),
        ("apply_available", apply_available),
        ("requires_git_reset", requires_git_reset),
    ):
        validate_bool(value, field)
    expected_enabled = not reasons
    if enabled is not expected_enabled:
        raise ValueError("enabled must equal not disabled_reasons")
    if requires_confirmation is not expected_enabled:
        raise ValueError("requires_confirmation must equal enabled")
    if apply_available or requires_git_reset:
        raise ValueError("read-only previews cannot apply or reset Git")


@dataclass(frozen=True)
class RewindPreview:
    kind: RewindKind
    checkpoint_id: str
    checkpoint_label: str
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    disabled_reasons: tuple[RewindDisabledReason, ...]
    enabled: bool
    requires_confirmation: bool
    apply_available: bool
    requires_git_reset: bool

    def __post_init__(self) -> None:
        if type(self.kind) is not RewindKind:
            raise TypeError("kind must be a RewindKind")
        validate_text(self.checkpoint_id, "checkpoint_id")
        validate_text(self.checkpoint_label, "checkpoint_label")
        if type(self.as_of) is not RewindAsOf:
            raise TypeError("as_of must be a RewindAsOf")
        validate_nonnegative_int(
            self.conversation_messages, "conversation_messages"
        )
        paths = _validate_paths(self.code_paths)
        reasons = _validate_reasons(self.disabled_reasons, self.kind)
        if any(reason in _CONVERSATION_REASONS for reason in reasons):
            if self.conversation_messages != 0:
                raise ValueError("disabled conversation facet must be empty")
        if any(reason in _CODE_REASONS for reason in reasons) and paths:
            raise ValueError("disabled code facet must be empty")
        _validate_preview_flags(
            reasons,
            self.enabled,
            self.requires_confirmation,
            self.apply_available,
            self.requires_git_reset,
        )


class RewindPreviewSource(Protocol):
    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage:
        ...

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview:
        ...


__all__ = [
    "RewindAsOf",
    "RewindCheckpointCandidate",
    "RewindCheckpointPage",
    "RewindDisabledReason",
    "RewindFacts",
    "RewindKind",
    "RewindPath",
    "RewindPreview",
    "RewindPreviewSource",
]
