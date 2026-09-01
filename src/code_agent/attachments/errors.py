from __future__ import annotations

from code_agent.core.attachments import AttachmentRef


class AttachmentError(ValueError):
    """A stable attachment ingestion or validation failure."""


class AttachmentIntegrityError(AttachmentError):
    """A content-addressed attachment failed path, size, or digest checks."""


class AttachmentCommittedError(AttachmentError):
    """Desired content exists, but a post-commit operation did not finish cleanly."""

    committed = True

    def __init__(self, message: str, reference: AttachmentRef) -> None:
        super().__init__(message)
        self.reference = reference
