from __future__ import annotations

import os
import uuid
from pathlib import Path

from .errors import WorkspaceError


class AtomicArtifactWriter:
    """Atomically write only into recorded manifest and blob directories."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.parents = {name: root / name for name in ("manifests", "blobs")}
        self.windows: object | None = None
        try:
            if os.name == "nt":
                from ._windows_artifact_write import WindowsArtifactWriter

                self.windows = WindowsArtifactWriter(root, self.parents)
                self.root_identity = ()
                self.identities: dict[str, tuple[int, ...]] = {}
            else:
                self.root_identity = _stat_identity(root)
                self.identities = {
                    name: _stat_identity(path) for name, path in self.parents.items()
                }
        except OSError as error:
            raise WorkspaceError("cannot record snapshot artifact directories") from error

    def write(self, target: Path, content: bytes) -> None:
        if type(content) is not bytes:
            raise TypeError("snapshot artifact content must be bytes")
        namespace = self._namespace(target)
        if os.name == "nt":
            assert self.windows is not None
            self.windows.write(namespace, target, content)  # type: ignore[attr-defined]
            return
        self._write_posix(namespace, target.name, content)

    def _namespace(self, target: Path) -> str:
        for name, parent in self.parents.items():
            if target.parent == parent and target.name == Path(target.name).name:
                return name
        raise WorkspaceError("snapshot write target is outside fixed artifact directories")

    def _write_posix(self, namespace: str, target_name: str, content: bytes) -> None:
        root_fd = parent_fd = temporary_fd = None
        temporary_name = f".snapshot-{uuid.uuid4().hex}"
        try:
            root_fd = os.open(self.root, _directory_flags())
            _verify_directory(root_fd, self.root_identity)
            parent_fd = os.open(namespace, _directory_flags(), dir_fd=root_fd)
            _verify_directory(parent_fd, self.identities[namespace])
            temporary_fd = os.open(
                temporary_name, _temporary_flags(), 0o600, dir_fd=parent_fd
            )
            _write_all(temporary_fd, content)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temporary_name = ""
            _best_effort_fsync(parent_fd)
        except WorkspaceError:
            raise
        except OSError as error:
            raise WorkspaceError(f"cannot atomically write snapshot artifact: {target_name}") from error
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name and parent_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except OSError:
                    pass
            if parent_fd is not None:
                os.close(parent_fd)
            if root_fd is not None:
                os.close(root_fd)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
        flags |= getattr(os, name, 0)
    return flags


def _temporary_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_NOFOLLOW", "O_CLOEXEC"):
        flags |= getattr(os, name, 0)
    return flags


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("snapshot artifact write made no progress")
        remaining = remaining[written:]


def _stat_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat(follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


def _verify_directory(descriptor: int, expected: tuple[int, ...]) -> None:
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != expected:
        raise WorkspaceError("snapshot artifact directory identity changed")


def _best_effort_fsync(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        pass
