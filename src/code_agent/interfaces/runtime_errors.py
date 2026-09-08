"""Bound failure messages before they enter the managed terminal transcript."""
from __future__ import annotations


def runtime_error_summary(error: BaseException) -> str:
    """Keep HTTP status across wrapped errors without rendering upstream bodies."""
    cause = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen and len(seen) < 8:
        seen.add(id(cause))
        status = getattr(cause, "status", None)
        if type(status) is int and 100 <= status <= 599:
            return f"Model request failed (HTTP {status}). Retry the request."
        cause = cause.__cause__
    message = " ".join(str(error).split())
    if "<html" in message.lower() or "<!doctype" in message.lower():
        message = "Request failed; upstream returned an HTML error page."
    return f"{type(error).__name__}: {message[:240]}"
