from __future__ import annotations

import base64

from .errors import WorkspaceError


class GitCommandError(WorkspaceError):
    """A structured failure from one of the fixed Git workspace operations."""

    def __init__(
        self,
        operation: str,
        argv: tuple[str, ...],
        returncode: int | None,
        stderr: str,
        message: str,
        *,
        stdout_bytes: bytes = b"",
        stderr_bytes: bytes | None = None,
    ) -> None:
        self.operation = operation
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = (
            stderr.encode("utf-8")
            if stderr_bytes is None
            else stderr_bytes
        )
        super().__init__(message)


class GitOutputLimitError(GitCommandError):
    """Raised after Git or snapshot work reaches the shared output budget."""

    def __init__(
        self,
        operation: str,
        argv: tuple[str, ...],
        returncode: int | None,
        stdout: bytes,
        stderr: bytes,
        max_output_bytes: int,
    ) -> None:
        self.max_output_bytes = max_output_bytes
        super().__init__(
            operation,
            argv,
            returncode,
            decode_git_output(stderr),
            f"git {operation} exceeded output limit of {max_output_bytes} bytes",
            stdout_bytes=stdout,
            stderr_bytes=stderr,
        )


class GitTimeoutError(GitCommandError):
    """Raised after a Git child exceeds its execution timeout."""

    def __init__(
        self,
        operation: str,
        argv: tuple[str, ...],
        returncode: int | None,
        stdout: bytes,
        stderr: bytes,
        timeout_s: float,
    ) -> None:
        self.timeout_s = timeout_s
        super().__init__(
            operation,
            argv,
            returncode,
            decode_git_output(stderr),
            f"git {operation} exceeded timeout of {timeout_s:g} seconds",
            stdout_bytes=stdout,
            stderr_bytes=stderr,
        )


def decode_git_output(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        encoded = base64.b64encode(value).decode("ascii")
        return f"[non-UTF-8 git output; base64={encoded}]"


def decode_git_text(
    value: bytes, operation: str, argv: tuple[str, ...]
) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitCommandError(
            operation,
            argv,
            0,
            "invalid UTF-8 output",
            f"git {operation} returned non-UTF-8 text output",
            stdout_bytes=value,
        ) from error
