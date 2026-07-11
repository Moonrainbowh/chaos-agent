from __future__ import annotations


class SessionError(RuntimeError):
    """Base exception for persistent session operations."""


class SessionNotFound(SessionError):
    """Raised when a requested thread or owned record does not exist."""


class SessionStorageError(SessionError):
    """Raised when a transactional storage operation fails."""


class SessionMigrationError(SessionError):
    """Raised when a schema cannot be migrated without data loss."""


class SessionCorruptionError(SessionError):
    """Raised when persisted schema or records cannot be trusted."""
