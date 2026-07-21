from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import (
    BinaryFileError,
    EditConflictError,
    FileTooLargeError,
    WorkspaceError,
)
from .paths import PathInput, WorkspacePathGuard
from ._secure_io import canonical_path_key
from ._snapshot_restore import execute_restore, preflight_restore

DEFAULT_SNAPSHOT_BYTES = 10_000_000
DEFAULT_MAX_FILE_BYTES = 10_000_000


@dataclass(frozen=True)
class EditPlan:
    relative_path: str
    before_sha256: str | None
    after_text: str
    diff: str
    existed: bool


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


class WorkspaceEditor:
    """Preview and atomically apply guarded single-file text edits."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool):
            raise TypeError("max_file_bytes must be an integer")
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.guard = guard
        self.max_file_bytes = max_file_bytes

    def plan_write(
        self,
        path: PathInput,
        after_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> EditPlan:
        """Build a write plan without changing the target file."""
        if not isinstance(after_text, str):
            raise TypeError("after_text must be text")
        target = self.guard.resolve(path, for_write=True)
        relative = self.guard.relative(target).as_posix()
        before, existed = _read_current(target, self.max_file_bytes)
        before_hash = _sha256(before) if existed else None
        _check_expected(before_hash, expected_sha256)
        before_text = _decode_existing(before, target) if existed else ""
        return EditPlan(
            relative_path=relative,
            before_sha256=before_hash,
            after_text=after_text,
            diff=_unified_diff(relative, before_text, after_text, existed),
            existed=existed,
        )

    def plan_replace(
        self,
        path: PathInput,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> EditPlan:
        """Plan replacement of exactly one non-empty text occurrence."""
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise TypeError("replacement values must be text")
        if not old_text:
            raise ValueError("old_text must be non-empty")
        target = self.guard.resolve(path, for_write=True)
        before, existed = _read_current(target, self.max_file_bytes)
        if not existed:
            raise WorkspaceError(f"cannot replace text in a missing file: {target}")
        before_hash = _sha256(before)
        _check_expected(before_hash, expected_sha256)
        before_text = _decode_existing(before, target)
        if before_text.count(old_text) != 1:
            raise ValueError("old_text must occur exactly once")
        relative = self.guard.relative(target).as_posix()
        after_text = before_text.replace(old_text, new_text, 1)
        return EditPlan(
            relative,
            before_hash,
            after_text,
            _unified_diff(relative, before_text, after_text, True),
            True,
        )

    def apply(self, plan: EditPlan) -> None:
        """Apply a non-stale plan as an atomic replacement."""
        if not isinstance(plan, EditPlan):
            raise TypeError("plan must be an EditPlan")
        target = self.guard.resolve(plan.relative_path, for_write=True)
        current, exists = _read_current(target, self.max_file_bytes)
        current_hash = _sha256(current) if exists else None
        if exists != plan.existed or current_hash != plan.before_sha256:
            raise EditConflictError(f"file changed after planning: {plan.relative_path}")
        _atomic_write(target, plan.after_text.encode("utf-8"))

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
            content, existed = _read_current(target, remaining)
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
        execute_restore(plan, self.guard)


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


def _read_current(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    if not path.exists():
        return b"", False
    if not path.is_file():
        raise WorkspaceError(f"not a regular file: {path}")
    try:
        if path.stat().st_size > max_bytes:
            raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {path}")
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {path}")
        return content, True
    except FileTooLargeError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot read file: {path}") from error


def _decode_existing(data: bytes, path: Path) -> str:
    if b"\0" in data:
        raise BinaryFileError(f"cannot edit binary file: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BinaryFileError(f"cannot edit non-UTF-8 file: {path}") from error


def _check_expected(actual: str | None, expected: str | None) -> None:
    if expected is not None and actual != expected:
        raise EditConflictError("current file hash does not match expected_sha256")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unified_diff(path: str, before: str, after: str, existed: bool) -> str:
    return "".join(
        difflib.unified_diff(
            _diff_lines(before),
            _diff_lines(after),
            fromfile=f"a/{path}" if existed else "/dev/null",
            tofile=f"b/{path}",
        )
    )


def _diff_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not text.endswith(("\n", "\r")):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def _atomic_write(target: Path, content: bytes) -> None:
    parent = target.parent
    if not parent.is_dir():
        raise WorkspaceError(f"parent directory does not exist: {parent}")
    previous_mode: int | None = None
    if target.is_file():
        previous_mode = stat.S_IMODE(target.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".code-agent-edit-", dir=parent, delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temporary_path, previous_mode)
        os.replace(temporary_path, target)
        temporary_path = None
    except OSError as error:
        raise WorkspaceError(f"cannot atomically write file: {target}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
