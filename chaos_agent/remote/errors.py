from __future__ import annotations


class RemoteInputError(ValueError):
    """A bounded request field is invalid."""


class RemoteNotFound(KeyError):
    """A requested catalog resource does not exist."""


class RemoteConflict(RuntimeError):
    """The requested operation conflicts with an observed task state."""
