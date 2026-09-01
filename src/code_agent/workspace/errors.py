from __future__ import annotations


class WorkspaceError(Exception):
    """Base exception for bounded workspace operations."""


class PathOutsideWorkspace(WorkspaceError):
    """Raised when a path cannot be contained by the workspace root."""


class WindowsLongPathError(WorkspaceError):
    """Windows is not configured to use the requested absolute path length."""


class WindowsFileBusyError(WorkspaceError):
    """A bounded Windows file mutation remained blocked or access-denied."""

    def __init__(
        self,
        path: object,
        operation: str,
        timeout_s: float,
        winerror: int,
    ) -> None:
        from pathlib import Path

        self.path = Path(path)  # type: ignore[arg-type]
        self.operation = operation
        self.timeout_s = timeout_s
        self.winerror = winerror
        if winerror == 5:
            detail = (
                "the file may be in use by another process, or permissions or "
                "attributes may block the operation"
            )
        else:
            detail = "the file is still in use or locked by another process"
        super().__init__(
            f"Windows could not {operation} within {timeout_s:g} seconds: "
            f"{self.path}; {detail}. Close programs using the file, check its "
            "permissions and attributes, then retry."
        )


class SensitivePathError(WorkspaceError):
    """Raised when direct access to protected or sensitive data is denied."""


class BinaryFileError(WorkspaceError):
    """Raised when a text operation encounters binary data."""


class FileTooLargeError(WorkspaceError):
    """Raised when an operation would exceed its byte limit."""


class EditConflictError(WorkspaceError):
    """Raised when an edit plan no longer matches on-disk state."""


class SearchTimeoutError(WorkspaceError):
    """Raised when a workspace search exceeds its global deadline."""


class WorkspaceScanLimitError(WorkspaceError):
    """Raised before a directory scan can exceed its entry budget."""


class SnapshotMissingError(WorkspaceError):
    """A referenced snapshot manifest or blob does not exist."""


class SnapshotIntegrityError(WorkspaceError):
    """A snapshot artifact exists but fails integrity validation."""
