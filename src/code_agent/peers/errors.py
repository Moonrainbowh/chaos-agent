from __future__ import annotations

class PeerError(RuntimeError):
    """Base error for same-user local peer messaging."""


class PeerIdentityError(PeerError):
    """Raised when a caller cannot prove the registered process identity."""


class PeerNotFoundError(PeerError):
    """Raised when no live peer matches a target or message identifier."""


class PeerAmbiguousError(PeerError):
    """Raised when a non-unique display name needs a stable ref."""

    def __init__(self, name: str, refs: tuple[str, ...]) -> None:
        super().__init__(f"peer name is ambiguous: {name}")
        self.name = name
        self.refs = refs


class PeerRateLimitError(PeerError):
    """Raised when one local peer exceeds its bounded send rate."""


class PeerQueueFullError(PeerError):
    """Raised when the recipient's queued or held inbox is full."""


class PeerClaimConflictError(PeerError):
    """Raised when a claim lease or expected message state no longer matches."""
