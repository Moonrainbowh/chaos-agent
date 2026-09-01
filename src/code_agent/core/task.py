from __future__ import annotations

import uuid
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, cast

from ._json import JSONValue
from .completion_contract import TaskIntent


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    VERIFYING = "verifying"
    WAITING_DECISION = "waiting_decision"
    PAUSED = "paused"
    COMPLETED = "completed"
    ACCEPTED_PARTIAL = "accepted_partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SUPERSEDED = "superseded"


_TERMINAL = {
    TaskStatus.COMPLETED,
    TaskStatus.ACCEPTED_PARTIAL,
    TaskStatus.FAILED,
    TaskStatus.SUPERSEDED,
}
_ALLOWED = {
    TaskStatus.CREATED: {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.INTERRUPTED},
    TaskStatus.RUNNING: {TaskStatus.VERIFYING, TaskStatus.PAUSED, TaskStatus.WAITING_DECISION, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED, TaskStatus.SUPERSEDED},
    TaskStatus.VERIFYING: {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.WAITING_DECISION, TaskStatus.ACCEPTED_PARTIAL, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED, TaskStatus.SUPERSEDED},
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.SUPERSEDED},
    TaskStatus.INTERRUPTED: {TaskStatus.RUNNING, TaskStatus.SUPERSEDED},
    TaskStatus.WAITING_DECISION: {TaskStatus.RUNNING, TaskStatus.ACCEPTED_PARTIAL, TaskStatus.FAILED, TaskStatus.SUPERSEDED},
}


def _text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(f"{name} must be non-blank text of at most 1024 characters")
    return value


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class TaskAuthorization:
    workspace_root: str
    allow_workspace_write: bool = True
    allow_local_execute: bool = True
    allow_network: bool = False
    allow_outside_workspace: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", cast(str, _text(self.workspace_root, "workspace_root")))
        for name in ("allow_workspace_write", "allow_local_execute", "allow_network", "allow_outside_workspace"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

    @classmethod
    def local_workspace(cls, workspace_root: str) -> TaskAuthorization:
        return cls(workspace_root)

    def to_dict(self) -> dict[str, JSONValue]:
        return {"workspace_root": self.workspace_root, "allow_workspace_write": self.allow_workspace_write, "allow_local_execute": self.allow_local_execute, "allow_network": self.allow_network, "allow_outside_workspace": self.allow_outside_workspace}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskAuthorization:
        return cls(**cast(dict[str, object], data))


@dataclass(frozen=True)
class TaskContract:
    objective: str
    authorization: TaskAuthorization
    max_active_seconds: int = 1_200
    max_repair_cycles: int = 3
    max_repeated_failure_signatures: int = 3
    intent: TaskIntent = TaskIntent.MODIFY
    profile_id: str | None = None
    model: str | None = None
    protocol: str | None = None
    endpoint_host: str | None = None
    agent_topology: str | None = None
    reasoning_effort: str | None = None
    runtime_mode: str | None = None
    runtime_selection_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "objective", cast(str, _text(self.objective, "objective")))
        if not isinstance(self.authorization, TaskAuthorization):
            raise TypeError("authorization must be a TaskAuthorization")
        if not isinstance(self.intent, TaskIntent):
            raise TypeError("intent must be a TaskIntent")
        profile_facts = (self.profile_id, self.model, self.protocol, self.endpoint_host)
        if any(value is not None for value in profile_facts) and not all(isinstance(value, str) and value.strip() for value in profile_facts):
            raise ValueError("profile audit facts must be complete non-blank text")
        runtime_facts = (
            self.agent_topology,
            self.reasoning_effort,
            self.runtime_mode,
            self.runtime_selection_digest,
        )
        if any(value is not None for value in runtime_facts):
            if not all(
                isinstance(value, str) and value.strip()
                for value in runtime_facts
            ):
                raise ValueError(
                    "runtime selection audit facts must be complete non-blank text"
                )
            if not all(
                isinstance(value, str) and value.strip()
                for value in profile_facts
            ):
                raise ValueError(
                    "runtime selection requires complete profile audit facts"
                )
            if self.agent_topology not in {"single", "team"}:
                raise ValueError("agent_topology must be single or team")
            if self.reasoning_effort not in {
                "low", "medium", "high", "xhigh", "max"
            }:
                raise ValueError("unsupported reasoning_effort")
            if self.runtime_mode not in {"low", "medium", "high", "ultra"}:
                raise ValueError("unsupported runtime_mode")
            if not re.fullmatch(
                r"[0-9a-f]{64}", self.runtime_selection_digest or ""
            ):
                raise ValueError(
                    "runtime_selection_digest must be a SHA-256 hex digest"
                )
        for name in ("max_active_seconds", "max_repair_cycles", "max_repeated_failure_signatures"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))

    def to_dict(self) -> dict[str, JSONValue]:
        return {"objective": self.objective, "authorization": self.authorization.to_dict(), "max_active_seconds": self.max_active_seconds, "max_repair_cycles": self.max_repair_cycles, "max_repeated_failure_signatures": self.max_repeated_failure_signatures, "intent": self.intent.value, "profile_id": self.profile_id, "model": self.model, "protocol": self.protocol, "endpoint_host": self.endpoint_host, "agent_topology": self.agent_topology, "reasoning_effort": self.reasoning_effort, "runtime_mode": self.runtime_mode, "runtime_selection_digest": self.runtime_selection_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskContract:
        return cls(objective=cast(str, data["objective"]), authorization=TaskAuthorization.from_dict(cast(Mapping[str, object], data["authorization"])), max_active_seconds=cast(int, data.get("max_active_seconds", 1200)), max_repair_cycles=cast(int, data.get("max_repair_cycles", 3)), max_repeated_failure_signatures=cast(int, data.get("max_repeated_failure_signatures", 3)), intent=TaskIntent(cast(str, data.get("intent", TaskIntent.MODIFY.value))), profile_id=cast(str | None, data.get("profile_id")), model=cast(str | None, data.get("model")), protocol=cast(str | None, data.get("protocol")), endpoint_host=cast(str | None, data.get("endpoint_host")), agent_topology=cast(str | None, data.get("agent_topology")), reasoning_effort=cast(str | None, data.get("reasoning_effort")), runtime_mode=cast(str | None, data.get("runtime_mode")), runtime_selection_digest=cast(str | None, data.get("runtime_selection_digest")))


@dataclass(frozen=True)
class TaskRecord:
    id: str
    thread_id: str
    contract: TaskContract
    status: TaskStatus = TaskStatus.CREATED
    stop_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", cast(str, _text(self.id, "id")))
        object.__setattr__(self, "thread_id", cast(str, _text(self.thread_id, "thread_id")))
        if not isinstance(self.contract, TaskContract) or not isinstance(self.status, TaskStatus):
            raise TypeError("task contract and status must be typed values")
        object.__setattr__(self, "stop_reason", _text(self.stop_reason, "stop_reason", optional=True))
        now = datetime.now(timezone.utc)
        created = self.created_at or now
        updated = self.updated_at or created
        if created.tzinfo is None or updated.tzinfo is None or updated < created:
            raise ValueError("task timestamps must be ordered and timezone-aware")
        object.__setattr__(self, "created_at", created.astimezone(timezone.utc))
        object.__setattr__(self, "updated_at", updated.astimezone(timezone.utc))

    @classmethod
    def new(cls, thread_id: str, objective: str, authorization: TaskAuthorization, **limits: int) -> TaskRecord:
        return cls(uuid.uuid4().hex, thread_id, TaskContract(objective, authorization, **limits))

    def transition(self, status: TaskStatus, reason: str | None = None) -> TaskRecord:
        if not isinstance(status, TaskStatus):
            raise TypeError("status must be a TaskStatus")
        if self.status in _TERMINAL or status not in _ALLOWED.get(self.status, set()):
            raise ValueError(f"illegal task transition: {self.status.value} -> {status.value}")
        return replace(self, status=status, stop_reason=_text(reason, "reason", optional=True), updated_at=datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, JSONValue]:
        return {"id": self.id, "thread_id": self.thread_id, "contract": self.contract.to_dict(), "status": self.status.value, "stop_reason": self.stop_reason, "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat()}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskRecord:
        created = cast(str, data["created_at"])
        updated = cast(str, data["updated_at"])
        return cls(cast(str, data["id"]), cast(str, data["thread_id"]), TaskContract.from_dict(cast(Mapping[str, object], data["contract"])), TaskStatus(cast(str, data["status"])), cast(str | None, data.get("stop_reason")), datetime.fromisoformat(created.replace("Z", "+00:00")), datetime.fromisoformat(updated.replace("Z", "+00:00")))
