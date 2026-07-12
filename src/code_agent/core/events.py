from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, cast

from ._json import JSONValue, freeze_mapping, plain, validate_json_mapping


class EventKind(str, Enum):
    TASK_CREATED = "task_created"
    TASK_STATUS_CHANGED = "task_status_changed"
    TASK_CHECKPOINT_CREATED = "task_checkpoint_created"
    TASK_BUDGET_WARNING = "task_budget_warning"
    TASK_PAUSED = "task_paused"
    TASK_DECISION_REQUIRED = "task_decision_required"
    RUN_STARTED = "run_started"
    TURN_STARTED = "turn_started"
    CONTEXT_BUILT = "context_built"
    MODEL_STARTED = "model_started"
    MODEL_EVENT = "model_event"
    ACTION_REQUESTED = "action_requested"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    MESSAGE_ADDED = "message_added"
    ERROR = "error"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AgentEvent:
    kind: EventKind
    payload: Mapping[str, JSONValue] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind):
            raise TypeError("kind must be an EventKind")
        validate_json_mapping(self.payload, "payload")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(
            self, "payload", freeze_mapping(self.payload, "payload")
        )
        object.__setattr__(
            self, "timestamp", self.timestamp.astimezone(timezone.utc)
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "kind": self.kind.value,
            "payload": plain(self.payload),
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AgentEvent:
        timestamp = cast(str, data["timestamp"])
        if timestamp.endswith("Z"):
            timestamp = f"{timestamp[:-1]}+00:00"
        return cls(
            kind=EventKind(cast(str, data["kind"])),
            payload=cast(Mapping[str, JSONValue], data.get("payload", {})),
            timestamp=datetime.fromisoformat(timestamp),
        )
