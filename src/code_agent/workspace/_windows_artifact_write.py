from __future__ import annotations

import uuid
from pathlib import Path

from ._windows_artifact_handles import (
    close_handle,
    directory_identity,
    flush_directory,
    flush_file,
    open_directory,
    verify_directory,
)
from ._windows_artifact_native import (
    create_relative_file,
    mark_delete,
    rename_relative,
    write_file,
)
from .errors import WorkspaceError


class WindowsArtifactWriter:
    """Use verified directory handles as capabilities for create and rename."""

    def __init__(self, root: Path, parents: dict[str, Path]) -> None:
        self.root = root
        self.parents = parents
        self.root_identity = directory_identity(root)
        self.identities = {
            name: directory_identity(path) for name, path in parents.items()
        }
        self._directory_flush_supported: bool | None = None

    @property
    def directory_flush_supported(self) -> bool | None:
        return self._directory_flush_supported

    def write(self, namespace: str, target: Path, content: bytes) -> None:
        root_handle: int | None = None
        parent_handle: int | None = None
        try:
            root_handle = open_directory(self.root)
            verify_directory(root_handle, self.root, self.root_identity)
            parent = self.parents[namespace]
            parent_handle = open_directory(parent)
            verify_directory(parent_handle, parent, self.identities[namespace])
            _before_relative_write(parent_handle, parent)
            _atomic_relative_write(parent_handle, target.name, content)
            self._directory_flush_supported = flush_directory(parent_handle)
        except WorkspaceError:
            raise
        except OSError as error:
            message = f"cannot atomically write snapshot artifact: {target.name}"
            raise WorkspaceError(message) from error
        finally:
            if parent_handle is not None:
                close_handle(parent_handle)
            if root_handle is not None:
                close_handle(root_handle)


def _before_relative_write(parent_handle: int, parent: Path) -> None:
    del parent_handle, parent


def _atomic_relative_write(parent_handle: int, target_name: str, content: bytes) -> None:
    temporary_name = f".snapshot-{uuid.uuid4().hex}"
    handle = create_relative_file(parent_handle, temporary_name)
    renamed = False
    try:
        write_file(handle, content)
        flush_file(handle)
        rename_relative(handle, parent_handle, target_name)
        renamed = True
    finally:
        if not renamed:
            # NTSTATUS cleanup failure is best effort so the write error stays primary.
            mark_delete(handle)
        close_handle(handle)
