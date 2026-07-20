from __future__ import annotations

from dataclasses import dataclass

from ._json import validate_identifier


def _validate_optional_identifier(value: object, label: str) -> None:
    if value is not None:
        validate_identifier(value, label)


@dataclass(frozen=True)
class ActionLineage:
    owner_thread_id: str
    task_id: str | None = None
    parent_request_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.owner_thread_id, "owner_thread_id")
        _validate_optional_identifier(self.task_id, "task_id")
        _validate_optional_identifier(self.parent_request_id, "parent_request_id")


@dataclass(frozen=True)
class ActionExecutionContext:
    owner_thread_id: str
    origin_thread_id: str
    request_id: str
    task_id: str | None = None
    parent_request_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.owner_thread_id, "owner_thread_id")
        validate_identifier(self.origin_thread_id, "origin_thread_id")
        validate_identifier(self.request_id, "request_id")
        _validate_optional_identifier(self.task_id, "task_id")
        _validate_optional_identifier(self.parent_request_id, "parent_request_id")
