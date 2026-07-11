from __future__ import annotations

from collections.abc import Iterable


class RuntimeErrorBase(RuntimeError):
    """Base exception for command runtime failures."""


class RuntimeUnavailable(RuntimeErrorBase):
    """Raised when an optional runtime executable is not installed."""


class RuntimeStartError(RuntimeErrorBase):
    """Raised when an available runtime cannot start a command."""


class ProcessTreeTerminationError(RuntimeErrorBase):
    """Raised when a process tree cannot be proven terminated."""

    def __init__(self, pid: int, failures: Iterable[str]) -> None:
        self.pid = pid
        self.failures = tuple(dict.fromkeys(failures))
        detail = "; ".join(self.failures) or "termination could not be verified"
        super().__init__(f"failed to terminate process tree {pid}: {detail}")
