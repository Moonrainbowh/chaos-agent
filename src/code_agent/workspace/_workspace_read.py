from __future__ import annotations

from ._guarded_read import _GuardedFileMissingError, read_guarded_file
from .errors import FileTooLargeError
from .paths import PathInput, WorkspacePathGuard


def read_current(
    guard: WorkspacePathGuard,
    path: PathInput,
    max_bytes: int,
) -> tuple[bytes, bool]:
    """Read current bytes from a verified handle, preserving initial absence."""
    try:
        content = read_guarded_file(
            path,
            guard,
            max_bytes,
            reject_known_oversize=True,
        )
    except _GuardedFileMissingError:
        return b"", False
    if len(content) > max_bytes:
        raise FileTooLargeError(f"file exceeds {max_bytes} bytes")
    return content, True
