from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .errors import WindowsFileBusyError, WorkspaceError


DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S = 0.5
READ_RETRY_WINERRORS = frozenset({32, 33})
DELETE_RETRY_WINERRORS = frozenset({32, 33})
# MoveFileEx, which backs os.replace on CPython, reports an open destination as
# access denied (5), while a locked source is normally a sharing violation (32).
REPLACE_RETRY_WINERRORS = frozenset({5, 32, 33})
_INITIAL_DELAY_S = 0.01
_MAX_DELAY_S = 0.1
_Result = TypeVar("_Result")


def validate_file_lock_timeout(timeout_s: float) -> float:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise TypeError("file_lock_timeout_s must be a number")
    value = float(timeout_s)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("file_lock_timeout_s must be positive and finite")
    return value


def retry_windows_file_operation(
    action: Callable[[], _Result],
    *,
    target: Path,
    operation: str,
    timeout_s: float = DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
    retry_winerrors: frozenset[int],
    validate: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> _Result:
    """Retry only operation-specific Windows sharing failures within a deadline."""
    timeout = validate_file_lock_timeout(timeout_s)
    if os.name != "nt":
        if validate is not None:
            validate()
        return action()
    deadline = clock() + timeout
    delay = _INITIAL_DELAY_S
    while True:
        if validate is not None:
            validate()
        try:
            return action()
        except (OSError, WorkspaceError) as error:
            if getattr(error, "publication_committed", False):
                raise
            winerror = windows_file_error_code(error)
            if winerror not in retry_winerrors:
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise WindowsFileBusyError(
                    target, operation, timeout, winerror
                ) from error
            sleeper(min(delay, remaining))
            delay = min(delay * 2, _MAX_DELAY_S)


def windows_file_error_code(error: BaseException) -> int | None:
    """Read a native code directly or through an explicit workspace cause."""
    if isinstance(error, OSError):
        return _direct_windows_file_error_code(error)
    if not isinstance(error, WorkspaceError):
        return None
    current = error.__cause__
    visited: set[int] = {id(error)}
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        code = _direct_windows_file_error_code(current)
        if code is not None:
            return code
        current = current.__cause__
    return None


def _direct_windows_file_error_code(error: BaseException) -> int | None:
    for name in ("winerror", "error_code"):
        value = getattr(error, name, None)
        if isinstance(value, int):
            return value
    return None
