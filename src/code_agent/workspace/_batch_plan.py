from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from ._batch_models import (
    BatchEditPlan,
    BatchOperation,
    DeletePlan,
    MovePlan,
    PlannedPathState,
)
from ._batch_observe import literal_relative, observe, require_state
from ._edit_plan import EditPlan
from ._secure_io import canonical_path_key
from .errors import BatchEditConflictError, EditConflictError, WorkspaceError
from .paths import PathInput


def plan_delete(
    editor: object,
    path: PathInput,
    *,
    expected_sha256: str | None = None,
) -> DeletePlan:
    relative = literal_relative(editor.guard, path)
    current = observe(editor, relative)
    if not current.state.existed:
        raise BatchEditConflictError(f"delete source is missing: {relative}")
    if expected_sha256 is not None and current.state.sha256 != expected_sha256:
        raise EditConflictError("current file hash does not match expected_sha256")
    diff = (
        f"--- a/{relative}\n+++ /dev/null\n"
        f"deleted sha256 {current.state.sha256}\n"
    )
    return DeletePlan(current.state, diff)


def plan_move(
    editor: object,
    source: PathInput,
    destination: PathInput,
    *,
    expected_source_sha256: str | None = None,
) -> MovePlan:
    source_relative = literal_relative(editor.guard, source)
    destination_relative = literal_relative(editor.guard, destination)
    if source_relative == destination_relative:
        raise ValueError("move source and destination must differ")
    source_observation = observe(editor, source_relative)
    if not source_observation.state.existed:
        raise BatchEditConflictError(
            f"move source is missing: {source_relative}"
        )
    if (
        expected_source_sha256 is not None
        and source_observation.state.sha256 != expected_source_sha256
    ):
        raise EditConflictError("current file hash does not match expected_sha256")
    _require_existing_destination_parent(editor, destination_relative)
    _reject_case_sensitive_move(editor, source_relative, destination_relative)
    destination_observation = observe(editor, destination_relative)
    source_key = canonical_path_key(source_relative)
    destination_key = canonical_path_key(destination_relative)
    case_only = source_key == destination_key
    aliases_source = bool(
        case_only
        and not destination_observation.state.existed
        and destination_observation.state.lookup_existed
        and destination_observation.identity == source_observation.identity
    )
    if destination_observation.state.existed or (
        destination_observation.state.lookup_existed and not aliases_source
    ):
        raise BatchEditConflictError(
            f"move destination exists: {destination_relative}"
        )
    destination_state = replace(
        destination_observation.state, aliases_source=aliases_source
    )
    diff = (
        f"rename from {source_relative}\n"
        f"rename to {destination_relative}\n"
        f"sha256 {source_observation.state.sha256}\n"
    )
    return MovePlan(
        source_observation.state,
        destination_state,
        diff,
        case_only,
    )


def plan_batch(
    editor: object,
    operations: Iterable[BatchOperation],
) -> BatchEditPlan:
    checked = tuple(operations)
    if not checked:
        raise ValueError("batch operations must be non-empty")
    paths: list[PlannedPathState] = []
    seen: set[str] = set()
    for operation in checked:
        local = _operation_states(editor, operation)
        keys = [canonical_path_key(state.relative_path) for state in local]
        if len(set(keys)) != len(keys) and not (
            type(operation) is MovePlan and operation.case_only and len(local) == 2
        ):
            raise ValueError(f"overlapping batch path: {local[-1].relative_path}")
        for key in set(keys):
            if key in seen:
                raise ValueError(f"overlapping batch path: {local[-1].relative_path}")
            seen.add(key)
        paths.extend(local)
    workspace_identity = editor.guard.root_identity
    identifier = _plan_id(workspace_identity, checked, tuple(paths))
    return BatchEditPlan(
        1,
        identifier,
        workspace_identity,
        checked,
        tuple(paths),
        "\n".join(operation.diff.rstrip("\n") for operation in checked) + "\n",
    )


def validate_batch_structure(plan: BatchEditPlan) -> None:
    if type(plan.operations) is not tuple or not plan.operations:
        raise ValueError("batch operations must be a non-empty tuple")
    if type(plan.paths) is not tuple or not all(
        type(state) is PlannedPathState for state in plan.paths
    ):
        raise ValueError("batch paths must be exact planned path states")
    cursor = 0
    seen: set[str] = set()
    for operation in plan.operations:
        count = 2 if type(operation) is MovePlan else 1
        local = plan.paths[cursor : cursor + count]
        if len(local) != count:
            raise ValueError("batch operations and paths do not match")
        _validate_operation_link(operation, local)
        keys = [canonical_path_key(state.relative_path) for state in local]
        case_alias = type(operation) is MovePlan and operation.case_only
        if len(set(keys)) != len(keys) and not case_alias:
            raise ValueError(f"overlapping batch path: {local[-1].relative_path}")
        for key in set(keys):
            if key in seen:
                raise ValueError(f"overlapping batch path: {local[-1].relative_path}")
            seen.add(key)
        cursor += count
    if cursor != len(plan.paths):
        raise ValueError("batch operations and paths do not match")


def _validate_operation_link(
    operation: BatchOperation, local: tuple[PlannedPathState, ...]
) -> None:
    if type(operation) is EditPlan:
        state = local[0]
        if (
            state.relative_path != operation.relative_path
            or state.existed != operation.existed
            or state.sha256 != operation.before_sha256
        ):
            raise ValueError("batch write does not match its planned path")
        return
    if type(operation) is DeletePlan:
        if local != (operation.source,):
            raise ValueError("batch delete does not match its planned path")
        return
    if type(operation) is not MovePlan:
        raise TypeError("batch operations must be exact plan values")
    if local != (operation.source, operation.destination):
        raise ValueError("batch move does not match its planned paths")
    same_key = canonical_path_key(local[0].relative_path) == canonical_path_key(
        local[1].relative_path
    )
    if operation.case_only != same_key:
        raise ValueError("batch move case-only marker does not match its paths")


def _operation_states(
    editor: object, operation: BatchOperation
) -> tuple[PlannedPathState, ...]:
    if type(operation) is EditPlan:
        current = observe(editor, operation.relative_path)
        if (
            current.state.existed != operation.existed
            or current.state.sha256 != operation.before_sha256
        ):
            raise BatchEditConflictError(
                f"batch write changed after planning: {operation.relative_path}"
            )
        return (current.state,)
    if type(operation) is DeletePlan:
        require_state(editor, operation.source)
        return (operation.source,)
    if type(operation) is MovePlan:
        require_state(editor, operation.source)
        require_state(editor, operation.destination)
        return (operation.source, operation.destination)
    raise TypeError("batch operations must be exact plan values")


def _plan_id(
    workspace_identity: tuple[int, int],
    operations: tuple[BatchOperation, ...],
    paths: tuple[PlannedPathState, ...],
) -> str:
    payload = {
        "schema_version": 1,
        "workspace_identity": list(workspace_identity),
        "paths": [vars(state) for state in paths],
        "operations": [_operation_payload(item) for item in operations],
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_payload(operation: BatchOperation) -> dict[str, object]:
    if type(operation) is EditPlan:
        assert operation.after_bytes is not None
        return {
            "kind": "write",
            "path": operation.relative_path,
            "after_sha256": hashlib.sha256(operation.after_bytes).hexdigest(),
        }
    if type(operation) is DeletePlan:
        return {"kind": "delete", "path": operation.source.relative_path}
    assert type(operation) is MovePlan
    return {
        "kind": "move",
        "source": operation.source.relative_path,
        "destination": operation.destination.relative_path,
        "case_only": operation.case_only,
    }


def _require_existing_destination_parent(editor: object, relative: str) -> None:
    parent = editor.guard.root / Path(relative).parent
    checked = editor.guard.resolve(parent, for_write=True)
    if not checked.exists() or not checked.is_dir():
        raise WorkspaceError(f"move destination parent is missing: {relative}")


def _reject_case_sensitive_move(
    editor: object, source_relative: str, destination_relative: str
) -> None:
    if os.name != "nt" or canonical_path_key(source_relative) != canonical_path_key(
        destination_relative
    ):
        return
    root = editor.guard.root
    source_parent = root / Path(source_relative).parent
    destination_parent = root / Path(destination_relative).parent
    if _directory_is_case_sensitive(source_parent) or _directory_is_case_sensitive(
        destination_parent
    ):
        raise WorkspaceError(
            "exact batch move is not supported in Windows case-sensitive directories"
        )


def _directory_is_case_sensitive(path: Path) -> bool:
    from ._windows_case_sensitivity import directory_is_case_sensitive

    return directory_is_case_sensitive(path)
