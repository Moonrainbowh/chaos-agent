from __future__ import annotations


class WorkspaceError(Exception):
    """Base exception for bounded workspace operations."""


class PathOutsideWorkspace(WorkspaceError):
    """Raised when a path cannot be contained by the workspace root."""


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
