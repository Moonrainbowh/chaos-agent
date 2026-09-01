from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping

from . import _secure_io as safety
from ._secure_modes import restore_mode
from ._windows_artifact_handles import handle_identity
from ._windows_artifact_native import mark_delete
from ._windows_guarded_open import (
    _close_all,
    _open_parent,
    _parent_paths,
)
from ._windows_replace_native import (
    DELETE as _DELETE,
    FILE_READ_ATTRIBUTES as _FILE_READ_ATTRIBUTES,
    FILE_WRITE_ATTRIBUTES as _FILE_WRITE_ATTRIBUTES,
    GENERIC_READ as _GENERIC_READ,
    close_handle as _close_handle,
    replace_file as _replace_file,
    open_file_guard as _open_file_guard,
    require_file_identity as _require_file_identity,
    set_handle_readonly as _set_handle_readonly,
)
from ._windows_replace_recovery import (
    recover_partial_replace as _recover_partial_replace,
    require_backup_identity as _require_backup_identity,
    reserve_backup as _reserve_backup,
    restore_displaced_winner as _restore_displaced_winner,
)
from .errors import EditConflictError, WorkspaceError
from .paths import WorkspacePathGuard


_PARTIAL_REPLACE_ERRORS = frozenset({1175, 1176, 1177})


def publish_windows_temp(
    temporary: Path,
    temporary_identity: safety.PathIdentity,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    *,
    context: str,
) -> None:
    """Publish while a no-write handle protects the final validation."""
    committed = False
    try:
        with _protected_parents(state, guard):
            temp_handle = _open_file_guard(
                temporary, _FILE_READ_ATTRIBUTES | _FILE_WRITE_ATTRIBUTES
            )
            primary: BaseException | None = None
            try:
                _require_file_identity(temp_handle, temporary, temporary_identity)
                if state.identity is None:
                    _publish_missing(
                        temporary, state, guard, created, validate, context
                    )
                else:
                    _publish_existing(
                        temporary, temp_handle, temporary_identity, state,
                        guard, created, validate, context,
                    )
                committed = True
            except BaseException as error:
                primary = error
                if getattr(error, "publication_committed", False):
                    committed = True
                raise
            finally:
                _close_or_attach(temp_handle, primary)
    except BaseException as error:
        if committed:
            setattr(error, "publication_committed", True)
        raise


def _publish_missing(
    temporary: Path,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    context: str,
) -> None:
    _validate_target(state, guard, created, validate, context)
    try:
        os.rename(temporary, state.target)
    except OSError:
        _validate_target(state, guard, created, validate, context)
        raise


def _publish_existing(
    temporary: Path,
    temp_handle: int,
    temporary_identity: safety.PathIdentity,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    context: str,
) -> None:
    assert state.identity is not None
    backup = _reserve_backup(state.target.parent)
    target_handle: int | None = None
    delete_handle: int | None = None
    published = False
    primary: BaseException | None = None
    try:
        try:
            target_handle = _open_file_guard(
                state.target, _GENERIC_READ | _FILE_WRITE_ATTRIBUTES
            )
            _require_file_identity(target_handle, state.target, state.identity)
            _validate_target(state, guard, created, validate, context)
            delete_handle = _open_file_guard(
                state.target, _DELETE | _FILE_READ_ATTRIBUTES
            )
            _require_file_identity(delete_handle, state.target, state.identity)
            readonly = not bool(restore_mode(state) & 0o200)
            if readonly:
                _set_handle_readonly(target_handle, False)
            try:
                _replace_file(state.target, temporary, backup)
            except OSError as error:
                code = getattr(error, "winerror", None)
                if code in _PARTIAL_REPLACE_ERRORS:
                    preserve_backup = True
                    preserve_backup = _recover_partial_replace(
                        code, temporary, temporary_identity, state, guard,
                        created, validate, backup, target_handle, context,
                    )
                    suffix = (
                        f"; preserved backup: {backup}"
                        if preserve_backup else ""
                    )
                    raise WorkspaceError(
                        f"Windows partially completed {context} file "
                        f"replacement ({code}); the original was preserved"
                        f"{suffix}"
                    ) from None
                if readonly:
                    _set_handle_readonly(target_handle, True)
                raise
            published = True
            _require_file_identity(temp_handle, state.target, temporary_identity)
            try:
                _require_backup_identity(backup, state.identity, delete_handle)
            except WorkspaceError:
                _set_handle_readonly(temp_handle, False)
                _restore_displaced_winner(
                    backup, temporary_identity, state, guard, created,
                )
                _set_handle_readonly(target_handle, readonly)
                raise EditConflictError(
                    f"target changed during {context}: {state.target}"
                ) from None
            _set_handle_readonly(temp_handle, readonly)
            if not mark_delete(delete_handle):
                raise WorkspaceError(
                    f"cannot remove owned replacement backup: {backup}"
                ) from None
        except BaseException as error:
            primary = error
            raise
        finally:
            _finish_existing(
                delete_handle, target_handle, primary,
            )
    except BaseException as error:
        if published:
            setattr(error, "publication_committed", True)
        raise


def _finish_existing(
    delete_handle: int | None,
    target_handle: int | None,
    primary: BaseException | None,
) -> None:
    cleanup: BaseException | None = None
    for handle in (delete_handle, target_handle):
        if handle is None:
            continue
        try:
            _close_handle(handle)
        except BaseException as error:
            cleanup = cleanup or error
    if cleanup is not None:
        if primary is None:
            raise cleanup
        _attach_cleanup(primary, cleanup)


def _close_or_attach(handle: int, primary: BaseException | None) -> None:
    try:
        _close_handle(handle)
    except BaseException as error:
        if primary is None:
            raise
        _attach_cleanup(primary, error)


def _attach_cleanup(primary: BaseException, cleanup: BaseException) -> None:
    if getattr(primary, "cleanup_error", None) is None:
        setattr(primary, "cleanup_error", cleanup)
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(f"Windows publication cleanup failure: {cleanup}")


def _validate_target(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    context: str,
) -> None:
    if validate is not None:
        validate()
    safety.verify_target_state(state, guard, created, context=context)


@contextmanager
def _protected_parents(
    state: safety.TargetState, guard: WorkspacePathGuard
) -> Iterator[None]:
    handles: list[int] = []
    primary: BaseException | None = None
    expected = {
        os.path.normcase(str(path)): identity
        for path, identity in state.parent.existing
    }
    try:
        for path in _parent_paths(state.target, guard.root):
            root_identity = guard.root_identity if path == guard.root else None
            handle = _open_parent(path, root_identity)
            handles.append(handle)
            identity = expected.get(os.path.normcase(str(path)))
            if identity is not None and handle_identity(handle) != (
                identity.device,
                identity.inode,
            ):
                raise WorkspaceError(f"parent changed during edit: {path}")
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        close_error = _close_all(handles)
        if close_error is not None and primary is None:
            raise WorkspaceError("cannot close protected parent directory") from close_error
