from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ._git_diff_snapshot import (
    SnapshotBudget,
    SnapshotBudgetExceeded,
    collect_diff_facets,
)
from ._git_errors import (
    GitCommandError,
    GitOutputLimitError,
    GitTimeoutError,
    decode_git_output as _decode_diagnostic,
    decode_git_text as _decode,
)
from .errors import WorkspaceError
from ._git_environment import isolated_git_environment
from ._git_process import collect_bounded_output
from .paths import PathInput, WorkspacePathGuard


DEFAULT_MAX_OUTPUT_BYTES = 1_000_000


@dataclass(frozen=True)
class _GitResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class GitDiffSnapshot:
    staged: str = ""
    unstaged: str = ""
    untracked: str = ""
    untracked_paths: tuple[str, ...] = ()


class GitWorkspace:
    """Expose only bounded repository detection, status, and diff operations."""

    def __init__(
        self,
        root: PathInput,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_s: float = 30.0,
    ) -> None:
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool):
            raise TypeError("max_output_bytes must be an integer")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be a number")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        git_executable = shutil.which("git")
        if git_executable is None:
            raise WorkspaceError("git executable was not found")
        self.guard = WorkspacePathGuard(root)
        self.root = self.guard.root
        self.max_output_bytes = max_output_bytes
        self.timeout_s = float(timeout_s)
        self._git_executable = git_executable

    def is_repository(self) -> bool:
        """Return whether root is inside a Git work tree."""
        result = self._invoke(
            "is_repository", ("rev-parse", "--is-inside-work-tree")
        )
        return (
            result.returncode == 0
            and _decode(result.stdout, "is_repository", result.argv).strip() == "true"
        )

    def status_porcelain(self) -> str:
        """Return machine-readable working tree status."""
        result = self._invoke("status", ("status", "--porcelain"))
        self._require_success("status", result)
        return _decode(result.stdout, "status", result.argv)

    def snapshot_paths(self, *, timeout_s: float | None = None) -> tuple[str, ...]:
        """Return tracked and non-ignored untracked paths for a snapshot."""
        result = self._invoke(
            "snapshot_paths",
            ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
            timeout_s=timeout_s,
        )
        self._require_success("snapshot_paths", result)
        return tuple(sorted(_decode_path_list(result.stdout)))

    def changed_snapshot_paths(self) -> tuple[str, ...]:
        """Return changed tracked paths and non-ignored untracked paths."""
        tracked = self._invoke(
            "changed_snapshot_paths",
            ("diff", "--name-only", "--no-renames", "-z", "HEAD", "--"),
        )
        self._require_success("changed_snapshot_paths", tracked)
        untracked = self._invoke(
            "changed_snapshot_paths",
            ("ls-files", "-z", "--others", "--exclude-standard"),
        )
        self._require_success("changed_snapshot_paths", untracked)
        return tuple(
            sorted(
                set(_decode_path_list(tracked.stdout))
                | set(_decode_path_list(untracked.stdout))
            )
        )

    def diff(self, paths: Iterable[PathInput] = ()) -> str:
        """Return a safe built-in Git diff, optionally restricted to paths."""
        if isinstance(paths, (str, os.PathLike)):
            supplied_paths: Iterable[PathInput] = (paths,)
        else:
            supplied_paths = paths
        relative_paths = tuple(
            self.guard.relative_literal(path).as_posix() for path in supplied_paths
        )
        arguments = (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--",
            *relative_paths,
        )
        result = self._invoke("diff", arguments)
        self._require_success("diff", result)
        return _decode(result.stdout, "diff", result.argv)

    def diff_snapshot(self, paths: Iterable[PathInput] = ()) -> GitDiffSnapshot:
        """Return staged, unstaged, and untracked diffs under one byte budget."""
        if isinstance(paths, (str, os.PathLike)):
            supplied_paths: Iterable[PathInput] = (paths,)
        else:
            supplied_paths = paths
        relative_paths = tuple(
            self.guard.relative_literal(path).as_posix() for path in supplied_paths
        )
        budget = SnapshotBudget(self.max_output_bytes)

        def invoke(
            operation: str, arguments: tuple[str, ...], limit: int | None
        ) -> _GitResult:
            return self._invoke(operation, arguments, max_output_bytes=limit)

        try:
            staged, unstaged, untracked, untracked_paths = collect_diff_facets(
                invoke,
                self._require_success,
                self.guard,
                budget,
                relative_paths,
            )
        except SnapshotBudgetExceeded as error:
            raise GitOutputLimitError(
                "diff_snapshot",
                (self._git_executable, "diff_snapshot"),
                None,
                b"",
                b"",
                self.max_output_bytes,
            ) from error
        return GitDiffSnapshot(
            staged=_decode_snapshot_patch(staged),
            unstaged=_decode_snapshot_patch(unstaged),
            untracked=untracked,
            untracked_paths=untracked_paths,
        )

    def _invoke(
        self,
        operation: str,
        arguments: tuple[str, ...],
        max_output_bytes: int | None = None,
        *,
        timeout_s: float | None = None,
    ) -> _GitResult:
        argv = (
            self._git_executable,
            "-c",
            "core.pager=cat",
            *(("-c", "core.longPaths=true") if os.name == "nt" else ()),
            "--literal-pathspecs",
            *arguments,
        )
        effective_timeout = self._effective_timeout(timeout_s)
        process = self._start_process(operation, argv)
        output_limit = self.max_output_bytes if max_output_bytes is None else max_output_bytes
        capture = collect_bounded_output(
            process, output_limit, effective_timeout
        )
        if capture.exceeded:
            raise GitOutputLimitError(
                operation,
                argv,
                capture.returncode,
                capture.stdout,
                capture.stderr,
                output_limit,
            )
        if capture.timed_out:
            raise GitTimeoutError(
                operation,
                argv,
                capture.returncode,
                capture.stdout,
                capture.stderr,
                effective_timeout,
            )
        if capture.read_error is not None:
            raise GitCommandError(
                operation,
                argv,
                capture.returncode,
                _decode_diagnostic(capture.stderr),
                f"git {operation} output could not be read: {capture.read_error}",
                stdout_bytes=capture.stdout,
                stderr_bytes=capture.stderr,
            ) from capture.read_error
        assert capture.returncode is not None
        return _GitResult(argv, capture.returncode, capture.stdout, capture.stderr)

    def _effective_timeout(self, timeout_s: float | None) -> float:
        if timeout_s is None:
            return self.timeout_s
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be a number")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        return min(self.timeout_s, float(timeout_s))

    def _start_process(
        self, operation: str, argv: tuple[str, ...]
    ) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(
                list(argv),
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=isolated_git_environment(),
                shell=False,
                bufsize=0,
            )
        except OSError as error:
            raise GitCommandError(
                operation,
                argv,
                None,
                str(error),
                f"git {operation} could not start: {error}",
            ) from error

    @staticmethod
    def _require_success(operation: str, result: _GitResult) -> None:
        if result.returncode == 0:
            return
        stderr = _decode_diagnostic(result.stderr)
        raise GitCommandError(
            operation,
            result.argv,
            result.returncode,
            stderr,
            f"git {operation} failed with exit code {result.returncode}: "
            f"{stderr.strip() or 'no error output'}",
            stdout_bytes=result.stdout,
            stderr_bytes=result.stderr,
        )
def _decode_snapshot_patch(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceError("git diff snapshot returned non-UTF-8 patch") from error


def _decode_path_list(value: bytes) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        return tuple(part.decode("utf-8") for part in value.split(b"\0") if part)
    except UnicodeDecodeError as error:
        raise GitCommandError(
            "snapshot_paths",
            (),
            None,
            "invalid UTF-8 path",
            "git snapshot_paths returned an undecodable path",
        ) from error
