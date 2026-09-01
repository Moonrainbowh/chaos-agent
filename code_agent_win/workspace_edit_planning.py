from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from code_agent.workspace._batch_models import (
    BatchEditPlan,
    DeletePlan,
    MovePlan,
)
from code_agent.workspace._edit_plan import EditPlan
from code_agent.workspace._secure_io import canonical_path_key
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.git import GitWorkspace

from code_agent_win.edit_plan_store import (
    StoredWorkspaceEditPlan,
    WorkspaceEditPlanStore,
)


MAX_EDIT_PLAN_PATHS = 32
_KINDS = frozenset({"write", "replace", "delete", "move"})
_ENCODINGS = frozenset({"auto", "windows-ansi", "windows-oem"})


class WorkspaceEditPlanningError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlannedWorkspaceEdit:
    stored: StoredWorkspaceEditPlan
    batch: BatchEditPlan
    operation_summaries: tuple[str, ...]
    risk_flags: tuple[str, ...]
    dirty_paths: tuple[str, ...]
    paths: tuple[str, ...]

    @property
    def plan_id(self) -> str:
        return self.stored.plan_id

    @property
    def plan_digest(self) -> str:
        return self.stored.plan_digest

    @property
    def operation_count(self) -> int:
        return len(self.batch.operations)

    @property
    def path_count(self) -> int:
        return len(self.paths)

    @property
    def combined_diff(self) -> str:
        return self.batch.diff


def create_stored_edit_plan(
    editor: WorkspaceEditor,
    store: WorkspaceEditPlanStore,
    operations: Sequence[Mapping[str, object]],
    *,
    workspace_fingerprint: str,
    owner_thread_id: str,
    task_id: str | None,
    git: GitWorkspace | object | None,
    supersedes_plan_id: str | None = None,
) -> PlannedWorkspaceEdit:
    """Build a zero-write batch and bind it to a trusted local store."""
    if not isinstance(editor, WorkspaceEditor):
        raise TypeError("editor must be a WorkspaceEditor")
    if not isinstance(store, WorkspaceEditPlanStore):
        raise TypeError("store must be a WorkspaceEditPlanStore")
    if not isinstance(operations, Sequence) or isinstance(
        operations, (str, bytes, bytearray)
    ):
        raise TypeError("operations must be a sequence of objects")
    if not operations or len(operations) > MAX_EDIT_PLAN_PATHS:
        raise WorkspaceEditPlanningError(
            "edit_plan_too_large", "operation count is outside 1..32"
        )
    planned = tuple(_plan_operation(editor, item) for item in operations)
    batch = editor.plan_batch(planned)
    if len(batch.paths) > MAX_EDIT_PLAN_PATHS:
        raise WorkspaceEditPlanningError(
            "edit_plan_too_large", "affected path count exceeds 32"
        )
    paths = tuple(state.relative_path for state in batch.paths)
    dirty = _dirty_paths(git, paths)
    untracked_existing = _untracked_existing_paths(git, batch)
    risks = _risk_flags(
        planned, batch, dirty, git is None, untracked_existing
    )
    stored = store.save(
        batch,
        plan_digest=batch.plan_id,
        workspace_fingerprint=workspace_fingerprint,
        owner_thread_id=owner_thread_id,
        task_id=task_id,
        risk_flags=risks,
        dirty_paths=dirty,
        supersedes_plan_id=supersedes_plan_id,
    )
    return PlannedWorkspaceEdit(
        stored,
        batch,
        tuple(_operation_summary(item) for item in planned),
        risks,
        dirty,
        paths,
    )


def _plan_operation(
    editor: WorkspaceEditor, raw: Mapping[str, object]
) -> EditPlan | DeletePlan | MovePlan:
    if not isinstance(raw, Mapping):
        raise WorkspaceEditPlanningError(
            "invalid_edit_operation", "edit operation must be an object"
        )
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise WorkspaceEditPlanningError(
            "invalid_edit_operation", "edit operation kind is invalid"
        )
    _require_fields(raw, kind)
    if kind == "write":
        return editor.plan_write(
            _text(raw, "path"),
            _text(raw, "content", allow_empty=True),
            encoding=_encoding(raw),
        )
    if kind == "replace":
        return editor.plan_replace(
            _text(raw, "path"),
            _text(raw, "old_text"),
            _text(raw, "new_text", allow_empty=True),
            encoding=_encoding(raw),
        )
    if kind == "delete":
        return editor.plan_delete(_text(raw, "path"))
    return editor.plan_move(
        _text(raw, "source_path"), _text(raw, "destination_path")
    )


def _require_fields(raw: Mapping[str, object], kind: str) -> None:
    required = {
        "write": {"kind", "path", "content"},
        "replace": {"kind", "path", "old_text", "new_text"},
        "delete": {"kind", "path"},
        "move": {"kind", "source_path", "destination_path"},
    }[kind]
    optional = {"encoding"} if kind in {"write", "replace"} else set()
    present = set(raw)
    if not required.issubset(present) or not present.issubset(required | optional):
        raise WorkspaceEditPlanningError(
            "invalid_edit_operation", f"fields are invalid for {kind}"
        )


def _text(
    raw: Mapping[str, object], name: str, *, allow_empty: bool = False
) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise WorkspaceEditPlanningError(
            "invalid_edit_operation", f"{name} must be text"
        )
    return value


def _encoding(raw: Mapping[str, object]) -> str:
    value = raw.get("encoding", "auto")
    if not isinstance(value, str) or value not in _ENCODINGS:
        raise WorkspaceEditPlanningError(
            "invalid_edit_operation", "encoding is invalid"
        )
    return value


def _dirty_paths(git: object | None, paths: tuple[str, ...]) -> tuple[str, ...]:
    if git is None:
        return ()
    changed = getattr(git, "changed_snapshot_paths", None)
    if not callable(changed):
        raise TypeError("git must expose changed_snapshot_paths")
    dirty_keys = {canonical_path_key(path) for path in changed()}
    return tuple(path for path in paths if canonical_path_key(path) in dirty_keys)


def _risk_flags(
    operations: tuple[EditPlan | DeletePlan | MovePlan, ...],
    batch: BatchEditPlan,
    dirty: tuple[str, ...],
    non_git: bool,
    untracked_existing: tuple[str, ...],
) -> tuple[str, ...]:
    flags: list[str] = []
    if dirty:
        flags.append("dirty_baseline")
    if non_git and any(state.existed for state in batch.paths):
        flags.append("non_git_existing")
    if untracked_existing:
        flags.append("untracked_existing")
    if any(type(item) is DeletePlan for item in operations):
        flags.append("delete")
    moves = tuple(item for item in operations if type(item) is MovePlan)
    if moves:
        flags.append("move")
    if any(item.case_only for item in moves):
        flags.append("case_only_move")
    return tuple(flags)


def _untracked_existing_paths(
    git: object | None, batch: BatchEditPlan
) -> tuple[str, ...]:
    if git is None:
        return ()
    existing = tuple(
        state.relative_path for state in batch.paths if state.existed
    )
    tracked_paths = getattr(git, "tracked_paths", None)
    if not callable(tracked_paths):
        raise TypeError("git must expose tracked_paths")
    tracked = {canonical_path_key(path) for path in tracked_paths(existing)}
    return tuple(
        path for path in existing if canonical_path_key(path) not in tracked
    )


def _operation_summary(operation: EditPlan | DeletePlan | MovePlan) -> str:
    if type(operation) is EditPlan:
        verb = "update" if operation.existed else "create"
        return f"{verb} {operation.relative_path}"
    if type(operation) is DeletePlan:
        return f"delete {operation.source.relative_path}"
    return (
        f"move {operation.source.relative_path} -> "
        f"{operation.destination.relative_path}"
    )


__all__ = [
    "MAX_EDIT_PLAN_PATHS",
    "PlannedWorkspaceEdit",
    "WorkspaceEditPlanningError",
    "create_stored_edit_plan",
]
