from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional

from code_agent.core._json import (
    JSONValue,
    freeze_mapping,
    validate_json_mapping,
)
from code_agent.core.models import Message


class ThreadStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _optional_text(value: object, name: str, *, blank: bool = False) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    if not blank and not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _metadata(value: object) -> Mapping[str, JSONValue]:
    validate_json_mapping(value, "metadata")
    return freeze_mapping(value, "metadata")


def _optional_sequence(value: object, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer or None")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class ThreadSummary:
    id: str
    title: Optional[str]
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime
    message_count: int
    last_message_preview: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "id"))
        object.__setattr__(self, "title", _optional_text(self.title, "title"))
        if not isinstance(self.status, ThreadStatus):
            raise TypeError("status must be a ThreadStatus")
        created = _utc(self.created_at, "created_at")
        updated = _utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        if isinstance(self.message_count, bool) or not isinstance(
            self.message_count, int
        ):
            raise TypeError("message_count must be an integer")
        if self.message_count < 0:
            raise ValueError("message_count must not be negative")
        object.__setattr__(
            self,
            "last_message_preview",
            _optional_text(
                self.last_message_preview, "last_message_preview", blank=True
            ),
        )


@dataclass(frozen=True)
class ThreadRelation:
    thread_id: str
    parent_thread_id: Optional[str]
    child_thread_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "thread_id", _required_text(self.thread_id, "thread_id"))
        object.__setattr__(
            self,
            "parent_thread_id",
            _optional_text(self.parent_thread_id, "parent_thread_id"),
        )
        children = tuple(
            _required_text(child, "child_thread_id") for child in self.child_thread_ids
        )
        if len(set(children)) != len(children):
            raise ValueError("child_thread_ids must be unique")
        object.__setattr__(self, "child_thread_ids", children)


@dataclass(frozen=True)
class MessageRecord:
    sequence: int
    thread_id: str
    message: Message
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        object.__setattr__(self, "thread_id", _required_text(self.thread_id, "thread_id"))
        if not isinstance(self.message, Message):
            raise TypeError("message must be a Message")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True)
class SkillActivationRecord:
    thread_id: str
    skill_id: str
    source: str
    digest: str
    activated_at: datetime

    def __post_init__(self) -> None:
        for name in ("thread_id", "skill_id", "source", "digest"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(
            self, "activated_at", _utc(self.activated_at, "activated_at")
        )


@dataclass(frozen=True)
class GoalRecord:
    id: str
    thread_id: str
    objective: str
    status: GoalStatus
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "id"))
        object.__setattr__(
            self, "thread_id", _required_text(self.thread_id, "thread_id")
        )
        object.__setattr__(
            self, "objective", _required_text(self.objective, "objective")
        )
        if not isinstance(self.status, GoalStatus):
            raise TypeError("status must be a GoalStatus")
        object.__setattr__(self, "metadata", _metadata(self.metadata))
        created = _utc(self.created_at, "created_at")
        updated = _utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)


@dataclass(frozen=True)
class CheckpointRecord:
    id: str
    thread_id: str
    label: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message_sequence: int | None = None
    event_sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "id"))
        object.__setattr__(
            self, "thread_id", _required_text(self.thread_id, "thread_id")
        )
        object.__setattr__(self, "label", _required_text(self.label, "label"))
        object.__setattr__(self, "metadata", _metadata(self.metadata))
        object.__setattr__(
            self, "created_at", _utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self,
            "message_sequence",
            _optional_sequence(self.message_sequence, "message_sequence"),
        )
        object.__setattr__(
            self,
            "event_sequence",
            _optional_sequence(self.event_sequence, "event_sequence"),
        )
