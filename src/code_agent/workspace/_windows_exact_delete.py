from __future__ import annotations

from pathlib import Path
from typing import Callable

from ._windows_artifact_native import mark_delete
from ._windows_exact_handles import (
    close_handles,
    open_locked_source,
    open_protected_parents,
)
from .errors import WorkspaceError
from .paths import WorkspacePathGuard
from ._secure_io import PathIdentity


def delete_exact(
    target: Path,
    guard: WorkspacePathGuard,
    *,
    expected_sha256: str,
    expected_size: int,
    max_bytes: int,
    validate: Callable[[], None],
    expected_identity: PathIdentity,
) -> None:
    parent_handles: list[int] = []
    source_handle: int | None = None
    primary: BaseException | None = None
    committed = False
    try:
        validate()
        parent_handles, _ = open_protected_parents(target, target, guard)
        source_handle = open_locked_source(
            target, expected_sha256, expected_size, max_bytes, expected_identity
        )
        if not mark_delete(source_handle):
            raise WorkspaceError(f"cannot delete exact owned file: {target}")
        committed = True
    except BaseException as error:
        primary = error
        if committed:
            setattr(error, "publication_committed", True)
        raise
    finally:
        cleanup = close_handles(source_handle, parent_handles)
        if cleanup is not None:
            if primary is None:
                if committed:
                    setattr(cleanup, "publication_committed", True)
                raise cleanup
            setattr(primary, "cleanup_error", cleanup)
