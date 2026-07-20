from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._rewind_model_validation import (
    normalize_utc,
    validate_bool,
    validate_exact_tuple,
    validate_optional_cursor,
    validate_text,
    validate_unique_strings,
)


@dataclass(frozen=True)
class RewindCheckpointCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

    def __post_init__(self) -> None:
        validate_text(self.checkpoint_id, "checkpoint_id")
        validate_text(self.label, "label")
        object.__setattr__(
            self, "created_at", normalize_utc(self.created_at, "created_at")
        )
        validate_bool(self.has_message_bound, "has_message_bound")
        validate_bool(self.has_code_anchor, "has_code_anchor")


@dataclass(frozen=True)
class RewindCheckpointPage:
    items: tuple[RewindCheckpointCandidate, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        items = validate_exact_tuple(
            self.items, "items", RewindCheckpointCandidate
        )
        if len(items) > 100:
            raise ValueError("items must contain at most 100 candidates")
        validate_unique_strings(
            tuple(item.checkpoint_id for item in items), "checkpoint_id"
        )
        validate_optional_cursor(self.next_cursor)
