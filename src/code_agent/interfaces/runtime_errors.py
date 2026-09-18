"""Bound failure messages before they enter the managed terminal transcript."""
from __future__ import annotations

import sqlite3

from code_agent.sessions.errors import SessionStorageError


def runtime_error_summary(error: BaseException) -> str:
    """Keep HTTP status across wrapped errors without rendering upstream bodies."""
    cause = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen and len(seen) < 8:
        seen.add(id(cause))
        if isinstance(cause, SessionStorageError):
            return _session_storage_summary(cause)
        status = getattr(cause, "status", None)
        if type(status) is int and 100 <= status <= 599:
            return f"Model request failed (HTTP {status}). Retry the request."
        if type(cause).__name__ in {
            "ProviderError",
            "ProviderConfigError",
            "ProviderProtocolError",
            "ProviderResponseLimitError",
        }:
            message = " ".join(str(cause).split())
            return f"{type(cause).__name__}: {message[:240]}"
        cause = cause.__cause__
    message = " ".join(str(error).split())
    if "<html" in message.lower() or "<!doctype" in message.lower():
        message = "Request failed; upstream returned an HTML error page."
    return f"{type(error).__name__}: {message[:240]}"


def _session_storage_summary(error: SessionStorageError) -> str:
    """Give a safe recovery action without exposing SQLite paths or SQL."""
    cause = error.__cause__
    if isinstance(cause, sqlite3.OperationalError):
        if "locked" in str(cause).casefold() or "busy" in str(cause).casefold():
            return "Session database is busy. Close other Chaos Agent sessions, then retry."
    if isinstance(cause, sqlite3.IntegrityError):
        return "Session data conflicts with an existing record. Start a new task or retry."
    return "Session storage failed. Restart Chaos Agent, then retry the task."


def explain_runtime_error(
    error: BaseException,
    *,
    status: str,
    changed: bool = False,
    checkpoint_saved: bool = False,
) -> str:
    """Translate a bounded runtime error into an actionable task explanation.

    ``changed`` and ``checkpoint_saved`` are host facts; callers must not infer
    them from exception text.  The legacy one-line summary remains available
    for machine-oriented callers.
    """
    if not isinstance(status, str) or not status.strip():
        raise ValueError("status must be non-blank text")
    lines = ["The task did not finish.", f"Status: {status.replace('_', ' ')}."]
    if changed:
        lines.append("Changes may already exist in the workspace; review them before retrying.")
    else:
        lines.append("No workspace change has been confirmed.")
    if checkpoint_saved:
        lines.append("A recovery checkpoint was saved.")
    lines.append("Error: " + runtime_error_summary(error))
    lines.append("Next: retry from the current workspace or inspect the failure details.")
    return "\n".join(lines)
