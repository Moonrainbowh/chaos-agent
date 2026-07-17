from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional

from code_agent.core._json import JSONValue

from ._rewind_model_base import (
    DEFAULT_REWIND_MAX_MUTATIONS,
    DEFAULT_REWIND_MAX_PATHS,
    MAX_REWIND_PAGE_SIZE,
    CoverageToken,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationStatus,
    bounded_int,
    optional_text,
    required_text,
    rewind_handle,
    rewind_paths,
    utc_datetime,
    validate_coverage,
    validate_identity,
)
from .models import CheckpointRecord


@dataclass(frozen=True)
class RewindMutationRecord:
    mutation_id: str
    sequence: int
    coverage: CoverageToken
    owner_thread_id: str
    origin_thread_id: str
    task_id: str | None
    parent_request_id: str | None
    request_id: str
    action_name: str
    status: RewindMutationStatus
    gap_reason: str | None
    snapshot_handle: Mapping[str, JSONValue] | None
    paths: tuple[RewindMutationPath, ...]
    created_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mutation_id", required_text(self.mutation_id, "mutation_id"))
        bounded_int(self.sequence, "sequence", minimum=1)
        validate_identity(self)
        if not isinstance(self.status, RewindMutationStatus):
            raise TypeError("status must be a RewindMutationStatus")
        reason = optional_text(self.gap_reason, "gap_reason")
        created = utc_datetime(self.created_at, "created_at")
        completed = (
            None if self.completed_at is None
            else utc_datetime(self.completed_at, "completed_at")
        )
        if completed is not None and completed < created:
            raise ValueError("completed_at must not precede created_at")
        if self.status is RewindMutationStatus.GAP:
            if reason is None or self.snapshot_handle is not None or self.paths or completed is None:
                raise ValueError("gap mutation fields are inconsistent")
            paths = rewind_paths(self.paths, empty=True)
            handle = None
        else:
            if reason is not None or self.snapshot_handle is None:
                raise ValueError("non-gap mutation fields are inconsistent")
            if (self.status is RewindMutationStatus.PREPARED) != (completed is None):
                raise ValueError("mutation completion time is inconsistent")
            paths = rewind_paths(self.paths)
            handle = rewind_handle(self.snapshot_handle)
        object.__setattr__(self, "gap_reason", reason)
        object.__setattr__(self, "snapshot_handle", handle)
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "completed_at", completed)


@dataclass(frozen=True)
class RewindCheckpointAnchor:
    coverage: CoverageToken
    owner_thread_id: str
    coverage_state: RewindCoverageState
    mutation_sequence: int

    def __post_init__(self) -> None:
        validate_coverage(self.coverage)
        object.__setattr__(
            self, "owner_thread_id", required_text(self.owner_thread_id, "owner_thread_id")
        )
        if not isinstance(self.coverage_state, RewindCoverageState):
            raise TypeError("coverage_state must be a RewindCoverageState")
        bounded_int(self.mutation_sequence, "mutation_sequence")


@dataclass(frozen=True)
class RewindCheckpointFact:
    checkpoint_id: str
    owner_thread_id: str
    coverage: CoverageToken
    mutation_sequence: int
    coverage_state: RewindCoverageState
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "checkpoint_id", required_text(self.checkpoint_id, "checkpoint_id")
        )
        object.__setattr__(
            self, "owner_thread_id", required_text(self.owner_thread_id, "owner_thread_id")
        )
        validate_coverage(self.coverage)
        bounded_int(self.mutation_sequence, "mutation_sequence")
        if not isinstance(self.coverage_state, RewindCoverageState):
            raise TypeError("coverage_state must be a RewindCoverageState")
        object.__setattr__(self, "created_at", utc_datetime(self.created_at, "created_at"))

    @property
    def workspace_fingerprint(self) -> str:
        return self.coverage.workspace_fingerprint

    @property
    def generation(self) -> int:
        return self.coverage.generation


@dataclass(frozen=True)
class RewindReadLimits:
    max_mutations: int = DEFAULT_REWIND_MAX_MUTATIONS
    max_paths: int = DEFAULT_REWIND_MAX_PATHS

    def __post_init__(self) -> None:
        bounded_int(
            self.max_mutations, "max_mutations", minimum=1,
            maximum=DEFAULT_REWIND_MAX_MUTATIONS,
        )
        bounded_int(
            self.max_paths, "max_paths", minimum=1, maximum=DEFAULT_REWIND_MAX_PATHS
        )


@dataclass(frozen=True)
class RewindObservationHeads:
    message_sequence: int
    event_sequence: int
    mutation_sequence: int
    coverage_generation: int | None
    coverage_state: RewindCoverageState | None

    def __post_init__(self) -> None:
        for name in ("message_sequence", "event_sequence", "mutation_sequence"):
            bounded_int(getattr(self, name), name)
        if (self.coverage_generation is None) != (self.coverage_state is None):
            raise ValueError("coverage head fields must both be present or absent")
        if self.coverage_generation is not None:
            bounded_int(self.coverage_generation, "coverage_generation", minimum=1)
        if self.coverage_state is not None and not isinstance(
            self.coverage_state, RewindCoverageState
        ):
            raise TypeError("coverage_state must be a RewindCoverageState")


@dataclass(frozen=True)
class RewindObservation:
    checkpoint: CheckpointRecord
    checkpoint_fact: RewindCheckpointFact | None
    conversation_message_count: int | None
    heads: RewindObservationHeads
    mutations: tuple[RewindMutationRecord, ...]
    limit_exceeded: bool

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, CheckpointRecord):
            raise TypeError("checkpoint must be a CheckpointRecord")
        if self.checkpoint_fact is not None and not isinstance(
            self.checkpoint_fact, RewindCheckpointFact
        ):
            raise TypeError("checkpoint_fact must be a RewindCheckpointFact or None")
        if self.conversation_message_count is not None:
            bounded_int(self.conversation_message_count, "conversation_message_count")
        if not isinstance(self.heads, RewindObservationHeads):
            raise TypeError("heads must be RewindObservationHeads")
        if type(self.mutations) is not tuple:
            raise TypeError("mutations must be a tuple")
        mutations = self.mutations
        if any(not isinstance(item, RewindMutationRecord) for item in mutations):
            raise TypeError("mutations must contain RewindMutationRecord values")
        if not isinstance(self.limit_exceeded, bool):
            raise TypeError("limit_exceeded must be a bool")
        if self.limit_exceeded and mutations:
            raise ValueError("limited observations cannot expose mutations")
        object.__setattr__(self, "mutations", mutations)


@dataclass(frozen=True)
class RewindCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "checkpoint_id", required_text(self.checkpoint_id, "checkpoint_id")
        )
        object.__setattr__(self, "label", required_text(self.label, "label"))
        object.__setattr__(self, "created_at", utc_datetime(self.created_at, "created_at"))
        if not isinstance(self.has_message_bound, bool):
            raise TypeError("has_message_bound must be a bool")
        if not isinstance(self.has_code_anchor, bool):
            raise TypeError("has_code_anchor must be a bool")


@dataclass(frozen=True)
class RewindCandidatePage:
    items: tuple[RewindCandidate, ...] = field(default_factory=tuple)
    next_cursor: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise TypeError("items must be a tuple")
        items = self.items
        if len(items) > MAX_REWIND_PAGE_SIZE:
            raise ValueError("candidate page is too large")
        if any(not isinstance(item, RewindCandidate) for item in items):
            raise TypeError("items must contain RewindCandidate values")
        cursor = self.next_cursor
        if cursor is not None:
            if type(cursor) is not str:
                raise TypeError("next_cursor must be a string or None")
            from ._rewind_codec import decode_rewind_cursor

            decode_rewind_cursor(cursor)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "next_cursor", cursor)
