from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ._text_codec import (
    DecodedText,
    TextCodecError,
    decode_text_bytes,
    encode_existing_text,
    encode_new_text,
)
from ._text_diff import unified_text_diff
from ._edit_plan import EditPlan
from ._batch_apply import PreparedBatchEdit
from ._batch_editor import BatchWorkspaceEditorMixin
from ._batch_models import (
    BatchApplyResult,
    BatchApplyStatus,
    BatchConflict,
    BatchEditPlan,
    DeletePlan,
    MovePlan,
    PathTransition,
    PlannedPathState,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryPathState,
)
from ._workspace_read import read_current
from .errors import (
    BinaryFileError,
    EditConflictError,
    FileTooLargeError,
    WorkspaceError,
    BatchEditConflictError,
    CrossVolumeMoveError,
)
from .paths import PathInput, WorkspacePathGuard
from ._secure_io import canonical_path_key, capture_target_state
from ._secure_replace import secure_atomic_write
from ._snapshot_restore import execute_restore, preflight_restore
from ._windows_file_locks import (
    DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
    READ_RETRY_WINERRORS,
    retry_windows_file_operation,
    validate_file_lock_timeout,
)

DEFAULT_SNAPSHOT_BYTES = 10_000_000
DEFAULT_MAX_FILE_BYTES = 10_000_000


@dataclass(frozen=True)
class SnapshotEntry:
    relative_path: str
    content: bytes | None
    existed: bool

    def __post_init__(self) -> None:
        if self.existed != (self.content is not None):
            raise ValueError("existing snapshot entries must contain bytes")
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("snapshot content must be bytes")


@dataclass(frozen=True)
class WorkspaceSnapshot:
    entries: tuple[SnapshotEntry, ...]


class WorkspaceEditor(BatchWorkspaceEditorMixin):
    """Preview and atomically apply guarded single-file text edits."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        file_lock_timeout_s: float = DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
    ) -> None:
        if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool):
            raise TypeError("max_file_bytes must be an integer")
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.guard = guard
        self.max_file_bytes = max_file_bytes
        self.file_lock_timeout_s = validate_file_lock_timeout(
            file_lock_timeout_s
        )

    def _read_current(self, target: Path, max_bytes: int) -> tuple[bytes, bool]:
        return retry_windows_file_operation(
            lambda: read_current(self.guard, target, max_bytes),
            target=target,
            operation="read file",
            timeout_s=self.file_lock_timeout_s,
            retry_winerrors=READ_RETRY_WINERRORS,
        )

    def plan_write(
        self,
        path: PathInput,
        after_text: str,
        *,
        expected_sha256: str | None = None,
        encoding: str = "auto",
    ) -> EditPlan:
        """Build a write plan without changing the target file."""
        if not isinstance(after_text, str):
            raise TypeError("after_text must be text")
        target = self.guard.resolve(path, for_write=True)
        relative = self.guard.relative(target).as_posix()
        before, existed = self._read_current(target, self.max_file_bytes)
        before_hash = _sha256(before) if existed else None
        _check_expected(before_hash, expected_sha256)
        decoded = _decode_existing(before, target, encoding) if existed else None
        before_text = decoded.text if decoded is not None else ""
        encoded = (
            encode_existing_text(after_text, decoded.format)
            if decoded is not None
            else encode_new_text(after_text, encoding)
        )
        return EditPlan(
            relative_path=relative,
            before_sha256=before_hash,
            after_text=encoded.text,
            diff=unified_text_diff(relative, before_text, encoded.text, existed),
            existed=existed,
            after_bytes=encoded.data,
            text_format=encoded.format,
        )

    def plan_replace(
        self,
        path: PathInput,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
        encoding: str = "auto",
    ) -> EditPlan:
        """Plan replacement of exactly one non-empty text occurrence."""
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise TypeError("replacement values must be text")
        if not old_text:
            raise ValueError("old_text must be non-empty")
        target = self.guard.resolve(path, for_write=True)
        before, existed = self._read_current(target, self.max_file_bytes)
        if not existed:
            raise WorkspaceError(f"cannot replace text in a missing file: {target}")
        before_hash = _sha256(before)
        _check_expected(before_hash, expected_sha256)
        decoded = _decode_existing(before, target, encoding)
        before_text = decoded.text
        if before_text.count(old_text) != 1:
            raise ValueError("old_text must occur exactly once")
        relative = self.guard.relative(target).as_posix()
        encoded = encode_existing_text(
            before_text.replace(old_text, new_text, 1), decoded.format
        )
        return EditPlan(
            relative,
            before_hash,
            encoded.text,
            unified_text_diff(relative, before_text, encoded.text, True),
            True,
            encoded.data,
            encoded.format,
        )

    def apply(self, plan: EditPlan) -> None:
        """Apply a non-stale plan as an atomic replacement."""
        if not isinstance(plan, EditPlan):
            raise TypeError("plan must be an EditPlan")
        target = self.guard.resolve(plan.relative_path, for_write=True)

        def validate_plan() -> None:
            current, exists = self._read_current(target, self.max_file_bytes)
            current_hash = _sha256(current) if exists else None
            if exists != plan.existed or current_hash != plan.before_sha256:
                raise EditConflictError(
                    f"file changed after planning: {plan.relative_path}"
                )

        validate_plan()
        state = capture_target_state(target, self.guard, context="edit")
        assert plan.after_bytes is not None
        secure_atomic_write(
            state,
            plan.after_bytes,
            self.guard,
            {},
            timeout_s=self.file_lock_timeout_s,
            validate=validate_plan,
            context="edit",
        )

    def snapshot(
        self,
        paths: Iterable[PathInput],
        max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
    ) -> WorkspaceSnapshot:
        """Copy current bytes and existence for guarded paths."""
        if not isinstance(max_total_bytes, int) or isinstance(max_total_bytes, bool):
            raise TypeError("max_total_bytes must be an integer")
        if max_total_bytes < 0:
            raise ValueError("max_total_bytes cannot be negative")
        entries: list[SnapshotEntry] = []
        seen: set[str] = set()
        total = 0
        for path in paths:
            target = self.guard.resolve(path)
            relative = self.guard.relative(target).as_posix()
            if relative in seen:
                raise ValueError(f"duplicate snapshot path: {relative}")
            seen.add(relative)
            remaining = max_total_bytes - total
            content, existed = self._read_current(target, remaining)
            total += len(content)
            if total > max_total_bytes:
                raise FileTooLargeError(
                    f"snapshot exceeds {max_total_bytes} total bytes"
                )
            entries.append(
                SnapshotEntry(relative, content if existed else None, existed)
            )
        return WorkspaceSnapshot(tuple(entries))

    def restore(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
    ) -> None:
        """Restore snapshotted bytes and remove paths absent in the snapshot."""
        if not isinstance(snapshot, WorkspaceSnapshot):
            raise TypeError("snapshot must be a WorkspaceSnapshot")
        if not isinstance(max_total_bytes, int) or isinstance(max_total_bytes, bool):
            raise TypeError("max_total_bytes must be an integer")
        if max_total_bytes < 0:
            raise ValueError("max_total_bytes cannot be negative")
        plan = preflight_restore(
            snapshot.entries,
            self.guard,
            self.max_file_bytes,
            max_total_bytes,
        )
        execute_restore(
            plan, self.guard, file_lock_timeout_s=self.file_lock_timeout_s
        )


WorkspaceEdits = WorkspaceEditor


def build_restore_snapshot(
    current_paths: Iterable[PathInput], target: WorkspaceSnapshot
) -> WorkspaceSnapshot:
    """Return a deterministic target plus tombstones for post-checkpoint files."""
    if not isinstance(target, WorkspaceSnapshot):
        raise TypeError("target must be a WorkspaceSnapshot")
    target_by_path: dict[str, SnapshotEntry] = {}
    for entry in target.entries:
        key = canonical_path_key(entry.relative_path)
        if key in target_by_path:
            raise ValueError(f"duplicate target path: {entry.relative_path}")
        target_by_path[key] = entry
    current: dict[str, str] = {}
    for path in current_paths:
        relative = os.fspath(path)
        key = canonical_path_key(relative)
        if key in current:
            raise ValueError(f"duplicate current path: {relative}")
        current[key] = relative
    for key in current.keys() - target_by_path.keys():
        target_by_path[key] = SnapshotEntry(current[key], None, False)
    return WorkspaceSnapshot(
        tuple(target_by_path[key] for key in sorted(target_by_path))
    )


def _decode_existing(data: bytes, path: Path, encoding: str) -> DecodedText:
    try:
        return decode_text_bytes(data, encoding)
    except TextCodecError as error:
        raise BinaryFileError(f"cannot edit undecodable text file: {path}") from error


def _check_expected(actual: str | None, expected: str | None) -> None:
    if expected is not None and actual != expected:
        raise EditConflictError("current file hash does not match expected_sha256")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
