from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ._batch_models import PlannedPathState
from ._batch_observe import require_state
from ._secure_io import PathIdentity, capture_target_state, same_path_state
from ._secure_mutation import secure_unlink
from ._secure_replace import secure_atomic_write
from ._windows_file_locks import (
    DELETE_RETRY_WINERRORS,
    REPLACE_RETRY_WINERRORS,
    retry_windows_file_operation,
)
from .errors import WorkspaceError


def write_bytes_exact(
    editor: object,
    expected: PlannedPathState,
    content: bytes,
    validate_batch: Callable[[], None],
    *,
    expected_identity: PathIdentity | None,
) -> None:
    target = editor.guard.root / Path(expected.relative_path)
    state = capture_target_state(target, editor.guard, context="batch write")
    if not same_path_state(state.identity, expected_identity):
        raise WorkspaceError(f"batch path identity drifted: {expected.relative_path}")

    def validate() -> None:
        validate_batch()
        require_state(editor, expected, require_identity=expected_identity)

    secure_atomic_write(
        state,
        content,
        editor.guard,
        {},
        timeout_s=editor.file_lock_timeout_s,
        validate=validate,
        context="batch edit",
    )


def delete_path_exact(
    editor: object,
    expected: PlannedPathState,
    validate_batch: Callable[[], None],
    *,
    expected_identity: PathIdentity,
) -> None:
    assert expected.sha256 is not None
    target = editor.guard.root / Path(expected.relative_path)

    def validate() -> None:
        validate_batch()
        require_state(editor, expected, require_identity=expected_identity)

    if os.name == "nt":
        from ._windows_exact_delete import delete_exact

        retry_windows_file_operation(
            lambda: delete_exact(
                target,
                editor.guard,
                expected_sha256=expected.sha256,
                expected_size=expected.size,
                max_bytes=editor.max_file_bytes,
                validate=validate,
                expected_identity=expected_identity,
            ),
            target=target,
            operation="delete exact batch file",
            timeout_s=editor.file_lock_timeout_s,
            retry_winerrors=DELETE_RETRY_WINERRORS,
            validate=validate,
        )
        return
    state = capture_target_state(target, editor.guard, context="batch delete")
    if not same_path_state(state.identity, expected_identity):
        raise WorkspaceError(f"batch path identity drifted: {expected.relative_path}")
    validate()
    secure_unlink(state, editor.guard, {}, timeout_s=editor.file_lock_timeout_s)


def move_path_exact(
    editor: object,
    source: PlannedPathState,
    destination: PlannedPathState,
    validate_batch: Callable[[], None],
    *,
    source_identity: PathIdentity,
    destination_identity: PathIdentity | None,
) -> None:
    if os.name != "nt":
        raise WorkspaceError("exact batch move is currently supported only on Windows")
    from ._windows_exact_move import move_no_replace

    assert source.sha256 is not None
    source_path = editor.guard.root / Path(source.relative_path)
    destination_path = editor.guard.root / Path(destination.relative_path)

    def validate() -> None:
        validate_batch()
        require_state(editor, source, require_identity=source_identity)
        current_destination = require_state(editor, destination)
        if not same_path_state(current_destination.identity, destination_identity):
            raise WorkspaceError(
                f"batch path identity drifted: {destination.relative_path}"
            )

    retry_windows_file_operation(
        lambda: move_no_replace(
            source_path,
            destination_path,
            editor.guard,
            expected_sha256=source.sha256,
            expected_size=source.size,
            max_bytes=editor.max_file_bytes,
            validate=validate,
            expected_identity=source_identity,
        ),
        target=source_path,
        operation="move exact batch file",
        timeout_s=editor.file_lock_timeout_s,
        retry_winerrors=REPLACE_RETRY_WINERRORS,
        validate=validate,
    )


def preflight_move_exact(
    editor: object,
    source: PlannedPathState,
    destination: PlannedPathState,
    *,
    source_identity: PathIdentity,
    destination_identity: PathIdentity | None,
) -> None:
    if os.name != "nt":
        raise WorkspaceError("exact batch move is currently supported only on Windows")
    from ._windows_exact_move import validate_same_volume

    assert source.sha256 is not None
    source_path = editor.guard.root / Path(source.relative_path)
    destination_path = editor.guard.root / Path(destination.relative_path)

    def validate() -> None:
        require_state(editor, source, require_identity=source_identity)
        current_destination = require_state(editor, destination)
        if not same_path_state(current_destination.identity, destination_identity):
            raise WorkspaceError(
                f"batch path identity drifted: {destination.relative_path}"
            )

    retry_windows_file_operation(
        lambda: validate_same_volume(
            source_path,
            destination_path,
            editor.guard,
            expected_sha256=source.sha256,
            expected_size=source.size,
            max_bytes=editor.max_file_bytes,
            validate=validate,
            expected_identity=source_identity,
        ),
        target=source_path,
        operation="preflight exact batch move",
        timeout_s=editor.file_lock_timeout_s,
        retry_winerrors=REPLACE_RETRY_WINERRORS,
        validate=validate,
    )
