from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .edits import (
    DEFAULT_SNAPSHOT_BYTES,
    EditPlan,
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from .errors import EditConflictError
from .paths import WorkspacePathGuard


_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class WorkspaceFileState:
    """Describe one canonical workspace path using raw-byte facts."""

    relative_path: str
    existed: bool
    sha256: str | None
    size: int

    def __post_init__(self) -> None:
        _validate_state_fields(self.relative_path, self.existed, self.sha256, self.size)


@dataclass(frozen=True)
class PreparedEditState:
    """Hold an edit's exact preimage and deterministic before/after facts."""

    snapshot: WorkspaceSnapshot
    before: WorkspaceFileState
    after: WorkspaceFileState

    def __post_init__(self) -> None:
        if type(self.snapshot) is not WorkspaceSnapshot:
            raise TypeError("snapshot must be a WorkspaceSnapshot")
        if type(self.before) is not WorkspaceFileState:
            raise TypeError("before must be a WorkspaceFileState")
        if type(self.after) is not WorkspaceFileState:
            raise TypeError("after must be a WorkspaceFileState")
        if self.before.relative_path != self.after.relative_path:
            raise ValueError("prepared edit paths must match")
        if not self.after.existed:
            raise ValueError("a prepared edit must produce an existing file")
        if type(self.snapshot.entries) is not tuple:
            raise TypeError("snapshot entries must be a tuple")
        if len(self.snapshot.entries) != 1:
            raise ValueError("a prepared edit snapshot must contain one entry")
        entry = self.snapshot.entries[0]
        if type(entry) is not SnapshotEntry:
            raise TypeError("snapshot must contain a SnapshotEntry")
        if _state_from_entry(entry) != self.before:
            raise ValueError("snapshot preimage must match the before state")


def prepare_edit_state(
    editor: WorkspaceEditor,
    plan: EditPlan,
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> PreparedEditState:
    """Observe a typed edit's current preimage without applying the plan."""
    _validate_editor(editor)
    _validate_plan(plan)
    _validate_budget(max_total_bytes)
    paths = _canonical_paths(editor, (plan.relative_path,))
    snapshot, states = _snapshot_states(editor, paths, max_total_bytes)
    before = states[0]
    if before.existed != plan.existed or before.sha256 != plan.before_sha256:
        raise EditConflictError(f"file changed after planning: {before.relative_path}")
    assert plan.after_bytes is not None
    after_bytes = plan.after_bytes
    after = WorkspaceFileState(
        before.relative_path,
        True,
        _sha256(after_bytes),
        len(after_bytes),
    )
    return PreparedEditState(snapshot, before, after)


def observe_file_states(
    editor: WorkspaceEditor,
    paths: tuple[str, ...],
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> tuple[WorkspaceFileState, ...]:
    """Return a complete, bounded observation sorted by canonical path."""
    _validate_editor(editor)
    _validate_budget(max_total_bytes)
    canonical = _canonical_paths(editor, paths)
    _, states = _snapshot_states(editor, canonical, max_total_bytes)
    return states


def relevant_path_digest(states: tuple[WorkspaceFileState, ...]) -> str:
    """Hash canonical state fields independently of caller ordering."""
    if type(states) is not tuple:
        raise TypeError("states must be a tuple")
    checked = _checked_states(states)
    payload = [
        {
            "relative_path": state.relative_path,
            "existed": state.existed,
            "sha256": state.sha256,
            "size": state.size,
        }
        for state in sorted(checked, key=lambda item: item.relative_path)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _validate_editor(editor: WorkspaceEditor) -> None:
    if type(editor) is not WorkspaceEditor:
        raise TypeError("editor must be a WorkspaceEditor")
    if type(editor.guard) is not WorkspacePathGuard:
        raise TypeError("editor guard must be a WorkspacePathGuard")
    if any(name in vars(editor.guard) for name in ("resolve", "relative")):
        raise TypeError("editor guard methods cannot be overridden")


def _validate_plan(plan: EditPlan) -> None:
    if type(plan) is not EditPlan:
        raise TypeError("plan must be an EditPlan")
    if type(plan.relative_path) is not str:
        raise TypeError("plan relative_path must be text")
    if type(plan.after_text) is not str or type(plan.diff) is not str:
        raise TypeError("plan text fields must be text")
    if type(plan.after_bytes) is not bytes:
        raise TypeError("plan after_bytes must be bytes")
    if type(plan.existed) is not bool:
        raise TypeError("plan existed must be boolean")
    if plan.existed:
        if type(plan.before_sha256) is not str or not _DIGEST.fullmatch(
            plan.before_sha256
        ):
            raise ValueError("existing plan must have a lowercase SHA-256")
    elif plan.before_sha256 is not None:
        raise ValueError("missing plan cannot have a before SHA-256")


def _validate_budget(max_total_bytes: int) -> None:
    if type(max_total_bytes) is not int:
        raise TypeError("max_total_bytes must be an integer")
    if max_total_bytes < 0:
        raise ValueError("max_total_bytes cannot be negative")


def _canonical_paths(
    editor: WorkspaceEditor,
    paths: tuple[str, ...],
) -> tuple[str, ...]:
    if type(paths) is not tuple:
        raise TypeError("paths must be a tuple")
    canonical: list[str] = []
    identities: set[str] = set()
    for raw in paths:
        if type(raw) is not str:
            raise TypeError("paths must contain strings")
        target = WorkspacePathGuard.resolve(editor.guard, raw)
        relative = WorkspacePathGuard.relative(editor.guard, target).as_posix()
        _validate_canonical_path(relative)
        identity = os.path.normcase(relative)
        if identity in identities:
            raise ValueError(f"duplicate workspace path: {relative}")
        identities.add(identity)
        canonical.append(relative)
    return tuple(sorted(canonical))


def _checked_states(
    states: tuple[WorkspaceFileState, ...],
) -> tuple[WorkspaceFileState, ...]:
    identities: set[str] = set()
    for state in states:
        if type(state) is not WorkspaceFileState:
            raise TypeError("states must contain WorkspaceFileState values")
        _validate_state_fields(
            state.relative_path,
            state.existed,
            state.sha256,
            state.size,
        )
        identity = os.path.normcase(state.relative_path)
        if identity in identities:
            raise ValueError(f"duplicate workspace state: {state.relative_path}")
        identities.add(identity)
    return states


def _state_from_entry(entry: SnapshotEntry) -> WorkspaceFileState:
    if type(entry) is not SnapshotEntry:
        raise TypeError("snapshot entries must be SnapshotEntry values")
    if type(entry.existed) is not bool:
        raise TypeError("snapshot entry existence must be boolean")
    if entry.existed:
        if type(entry.content) is not bytes:
            raise TypeError("existing snapshot entries must contain bytes")
        return WorkspaceFileState(
            entry.relative_path,
            True,
            _sha256(entry.content),
            len(entry.content),
        )
    if entry.content is not None:
        raise TypeError("missing snapshot entries cannot contain bytes")
    return WorkspaceFileState(entry.relative_path, False, None, 0)


def _snapshot_states(
    editor: WorkspaceEditor,
    paths: tuple[str, ...],
    max_total_bytes: int,
) -> tuple[WorkspaceSnapshot, tuple[WorkspaceFileState, ...]]:
    snapshot = WorkspaceEditor.snapshot(
        editor,
        paths,
        max_total_bytes=max_total_bytes,
    )
    if type(snapshot) is not WorkspaceSnapshot:
        raise TypeError("editor snapshot must be a WorkspaceSnapshot")
    if type(snapshot.entries) is not tuple or len(snapshot.entries) != len(paths):
        raise ValueError("editor snapshot must match the requested path count")
    states = tuple(_state_from_entry(entry) for entry in snapshot.entries)
    if tuple(state.relative_path for state in states) != paths:
        raise ValueError("editor snapshot paths must match the request")
    return snapshot, states


def _validate_state_fields(
    relative_path: str,
    existed: bool,
    digest: str | None,
    size: int,
) -> None:
    _validate_canonical_path(relative_path)
    if type(existed) is not bool:
        raise TypeError("existed must be boolean")
    if type(size) is not int:
        raise TypeError("size must be an integer")
    if size < 0:
        raise ValueError("size cannot be negative")
    if existed and (type(digest) is not str or not _DIGEST.fullmatch(digest)):
        raise ValueError("existing state must have a lowercase SHA-256")
    if not existed and (digest is not None or size != 0):
        raise ValueError("missing state cannot declare bytes")


def _validate_canonical_path(value: str) -> None:
    if type(value) is not str:
        raise TypeError("relative_path must be text")
    windows = PureWindowsPath(value)
    if (
        not value
        or "\0" in value
        or ":" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or Path(value).is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("relative_path must be canonical relative POSIX text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("relative_path must be strict UTF-8 text") from error


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
