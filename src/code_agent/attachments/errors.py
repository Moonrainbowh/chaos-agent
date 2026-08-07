class AttachmentError(ValueError):
    """A stable attachment ingestion or validation failure."""


class AttachmentIntegrityError(AttachmentError):
    """A content-addressed attachment failed path, size, or digest checks."""
