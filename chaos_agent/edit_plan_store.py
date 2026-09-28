from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum


_DIGEST = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")


class StoredPlanStatus(str, Enum):
    PLANNED = "planned"
    APPLYING = "applying"
    APPLIED = "applied"
    CONFLICTED = "conflicted"
    RECOVERY_REQUIRED = "recovery_required"
    SUPERSEDED = "superseded"


class EditPlanStoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoredWorkspaceEditPlan:
    plan_id: str
    plan_digest: str
    workspace_fingerprint: str
    owner_thread_id: str
    task_id: str | None
    plan: object
    risk_flags: tuple[str, ...] = ()
    dirty_paths: tuple[str, ...] = ()
    status: StoredPlanStatus = StoredPlanStatus.PLANNED
    supersedes_plan_id: str | None = None


class WorkspaceEditPlanStore:
    """Keep locally generated plans immutable and bound to their owner."""

    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._plans: dict[str, StoredWorkspaceEditPlan] = {}
        self._lock = threading.RLock()

    def save(
        self,
        plan: object,
        *,
        plan_digest: str,
        workspace_fingerprint: str,
        owner_thread_id: str,
        task_id: str | None,
        risk_flags: tuple[str, ...] = (),
        dirty_paths: tuple[str, ...] = (),
        supersedes_plan_id: str | None = None,
    ) -> StoredWorkspaceEditPlan:
        digest = _require_digest("plan_digest", plan_digest)
        fingerprint = _require_digest(
            "workspace_fingerprint", workspace_fingerprint
        )
        owner = _require_text("owner_thread_id", owner_thread_id)
        task = _optional_text("task_id", task_id)
        risks = _text_tuple("risk_flags", risk_flags)
        dirty = _text_tuple("dirty_paths", dirty_paths)
        with self._lock:
            superseded = None
            if supersedes_plan_id is not None:
                superseded = self._owned_planned(
                    supersedes_plan_id, fingerprint, owner, task
                )
            plan_id = self._new_identifier()
            stored = StoredWorkspaceEditPlan(
                plan_id,
                digest,
                fingerprint,
                owner,
                task,
                plan,
                risks,
                dirty,
                StoredPlanStatus.PLANNED,
                supersedes_plan_id,
            )
            if superseded is not None:
                self._plans[superseded.plan_id] = replace(
                    superseded, status=StoredPlanStatus.SUPERSEDED
                )
            self._plans[plan_id] = stored
            return stored

    def get(self, plan_id: str) -> StoredWorkspaceEditPlan:
        identifier = _require_identifier(plan_id)
        with self._lock:
            try:
                return self._plans[identifier]
            except KeyError as error:
                raise EditPlanStoreError(
                    "edit_plan_not_found", "edit plan was not found"
                ) from error

    def require_applicable(
        self,
        plan_id: str,
        plan_digest: str,
        *,
        workspace_fingerprint: str,
        owner_thread_id: str,
        task_id: str | None,
    ) -> StoredWorkspaceEditPlan:
        stored = self.get(plan_id)
        if stored.plan_digest != _require_digest("plan_digest", plan_digest):
            raise EditPlanStoreError(
                "edit_plan_digest_mismatch", "edit plan digest does not match"
            )
        fingerprint = _require_digest(
            "workspace_fingerprint", workspace_fingerprint
        )
        if stored.workspace_fingerprint != fingerprint:
            raise EditPlanStoreError(
                "edit_plan_workspace_mismatch", "edit plan belongs to another workspace"
            )
        owner = _require_text("owner_thread_id", owner_thread_id)
        task = _optional_text("task_id", task_id)
        if stored.owner_thread_id != owner or stored.task_id != task:
            raise EditPlanStoreError(
                "edit_plan_owner_mismatch", "edit plan belongs to another owner"
            )
        if stored.status is not StoredPlanStatus.PLANNED:
            raise EditPlanStoreError(
                "edit_plan_not_applicable", "edit plan is no longer applicable"
            )
        return stored

    def begin_apply(self, plan_id: str) -> StoredWorkspaceEditPlan:
        with self._lock:
            stored = self.get(plan_id)
            if stored.status is not StoredPlanStatus.PLANNED:
                raise EditPlanStoreError(
                    "edit_plan_not_applicable", "edit plan is no longer applicable"
                )
            updated = replace(stored, status=StoredPlanStatus.APPLYING)
            self._plans[stored.plan_id] = updated
            return updated

    def settle(
        self, plan_id: str, status: StoredPlanStatus
    ) -> StoredWorkspaceEditPlan:
        if status not in {
            StoredPlanStatus.APPLIED,
            StoredPlanStatus.CONFLICTED,
            StoredPlanStatus.RECOVERY_REQUIRED,
        }:
            raise ValueError("status must be a terminal apply status")
        with self._lock:
            stored = self.get(plan_id)
            if stored.status is status:
                return stored
            if stored.status is not StoredPlanStatus.APPLYING:
                raise EditPlanStoreError(
                    "edit_plan_not_applicable", "edit plan cannot be settled"
                )
            updated = replace(stored, status=status)
            self._plans[stored.plan_id] = updated
            return updated

    def _owned_planned(
        self, plan_id: str, fingerprint: str, owner: str, task: str | None
    ) -> StoredWorkspaceEditPlan:
        stored = self.get(plan_id)
        if (
            stored.workspace_fingerprint != fingerprint
            or stored.owner_thread_id != owner
            or stored.task_id != task
        ):
            raise EditPlanStoreError(
                "edit_plan_owner_mismatch", "superseded plan belongs elsewhere"
            )
        if stored.status is not StoredPlanStatus.PLANNED:
            raise EditPlanStoreError(
                "edit_plan_not_applicable", "superseded plan is not applicable"
            )
        return stored

    def _new_identifier(self) -> str:
        plan_id = _require_identifier(self._id_factory())
        if plan_id in self._plans:
            raise EditPlanStoreError(
                "edit_plan_id_collision", "edit plan identifier already exists"
            )
        return plan_id


def _require_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _require_identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("plan_id must be 32 lowercase hex characters")
    return value


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    return value


def _optional_text(name: str, value: object) -> str | None:
    return None if value is None else _require_text(name, value)


def _text_tuple(name: str, value: object) -> tuple[str, ...]:
    if type(value) is not tuple or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise TypeError(f"{name} must be a tuple of non-blank strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must be unique")
    return value


__all__ = [
    "EditPlanStoreError",
    "StoredPlanStatus",
    "StoredWorkspaceEditPlan",
    "WorkspaceEditPlanStore",
]
