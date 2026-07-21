from __future__ import annotations

import re
from dataclasses import dataclass

from code_agent.sessions.workspace_models import RewindMode, RewindOperationStatus


MAX_PREVIEW_PATHS = 100
MAX_PREVIEW_PATH_BYTES = 16_384
_IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(Exception):
    """Base error for checkpoint orchestration."""


class RewindError(CheckpointError):
    """Base error for a failed Rewind operation."""


class RewindConfirmationRequired(RewindError):
    """Raised unless execution receives the literal boolean True."""


class RewindConflict(RewindError):
    """Raised when preview facts no longer match current durable facts."""


class RewindUnavailable(RewindError):
    """Raised when the selected mode requires unavailable code bytes."""


class RewindRecoveryRequired(RewindError):
    """Raised when compensation cannot prove that the lineage is safe."""

    def __init__(self, paths: tuple[str, ...] = ()) -> None:
        self.paths = paths
        suffix = "" if not paths else f": {', '.join(paths)}"
        super().__init__(f"rewind recovery required{suffix}")


@dataclass(frozen=True)
class RewindPreview:
    operation_id: str
    task_id: str
    lineage_id: str
    checkpoint_id: str
    mode: RewindMode
    fingerprint: str
    restore_count: int
    delete_count: int
    total_bytes: int
    paths: tuple[str, ...]
    code_available: bool

    def __post_init__(self) -> None:
        for name in ("operation_id", "task_id", "lineage_id", "checkpoint_id"):
            if not isinstance(getattr(self, name), str) or not _IDENTIFIER.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a 32-character lowercase identifier")
        if not isinstance(self.mode, RewindMode):
            raise TypeError("mode must be a RewindMode")
        if not isinstance(self.fingerprint, str) or not _DIGEST.fullmatch(self.fingerprint):
            raise ValueError("fingerprint must be a SHA-256 digest")
        for name in ("restore_count", "delete_count", "total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.paths, tuple) or len(self.paths) > MAX_PREVIEW_PATHS:
            raise ValueError("preview paths exceed the display limit")
        if sum(len(path.encode("utf-8")) for path in self.paths) > MAX_PREVIEW_PATH_BYTES:
            raise ValueError("preview paths exceed the byte limit")
        if not all(isinstance(path, str) and path for path in self.paths):
            raise TypeError("preview paths must be non-blank text")
        if not isinstance(self.code_available, bool):
            raise TypeError("code_available must be a bool")


@dataclass(frozen=True)
class RewindResult:
    operation_id: str
    task_id: str
    replacement_task_id: str | None
    status: RewindOperationStatus

    def __post_init__(self) -> None:
        for name in ("operation_id", "task_id"):
            if not isinstance(getattr(self, name), str) or not _IDENTIFIER.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a 32-character lowercase identifier")
        if self.replacement_task_id is not None and not _IDENTIFIER.fullmatch(
            self.replacement_task_id
        ):
            raise ValueError("replacement_task_id must be a lowercase identifier")
        if not isinstance(self.status, RewindOperationStatus):
            raise TypeError("status must be a RewindOperationStatus")
