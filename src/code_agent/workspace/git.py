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
    decode_git_output as _decode,
)
from .errors import WorkspaceError
from ._git_process import ProcessCapture, collect_bounded_output
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
        return result.returncode == 0 and _decode(result.stdout).strip() == "true"

    def status_porcelain(self) -> str:
        """Return machine-readable working tree status."""
        result = self._invoke("status", ("status", "--porcelain"))
        self._require_success("status", result)
        return _decode(result.stdout)

    def diff(self, paths: Iterable[PathInput] = ()) -> str:
        """Return a safe built-in Git diff, optionally restricted to paths."""
        if isinstance(paths, (str, os.PathLike)):
            supplied_paths: Iterable[PathInput] = (paths,)
        else:
            supplied_paths = paths
        relative_paths = tuple(
            self.guard.relative(path).as_posix() for path in supplied_paths
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
        return _decode(result.stdout)

    def diff_snapshot(
        self, paths: Iterable[PathInput] = ()
    ) -> GitDiffSnapshot:
        """Return staged, unstaged, and untracked diff facets under one budget."""
        if isinstance(paths, (str, os.PathLike)):
            supplied_paths: Iterable[PathInput] = (paths,)
        else:
            supplied_paths = paths
        relative = tuple(self.guard.relative(path).as_posix() for path in supplied_paths)
        budget = SnapshotBudget(self.max_output_bytes)
        try:
            staged, unstaged, untracked, untracked_paths = collect_diff_facets(
                self._invoke,
                self._require_success,
                self.guard,
                budget,
                relative,
            )
        except SnapshotBudgetExceeded as error:
            raise self._snapshot_limit_error() from error
        return GitDiffSnapshot(
            _decode(staged), _decode(unstaged), untracked, untracked_paths
        )

    def _snapshot_limit_error(self) -> GitOutputLimitError:
        argv = (self._git_executable, "diff_snapshot")
        return GitOutputLimitError(
            "diff_snapshot", argv, None, b"", b"", self.max_output_bytes
        )

    def _invoke(
        self,
        operation: str,
        arguments: tuple[str, ...],
        max_output_bytes: int | None = None,
    ) -> _GitResult:
        argv = (
            self._git_executable,
            "-c",
            "core.pager=cat",
            "--literal-pathspecs",
            *arguments,
        )
        process = self._start_process(operation, argv)
        output_limit = self.max_output_bytes if max_output_bytes is None else max_output_bytes
        capture = collect_bounded_output(process, output_limit, self.timeout_s)
        return self._finish_invoke(operation, argv, capture)

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

    def _finish_invoke(
        self, operation: str, argv: tuple[str, ...], capture: ProcessCapture
    ) -> _GitResult:
        if capture.exceeded:
            raise GitOutputLimitError(
                operation,
                argv,
                capture.returncode,
                capture.stdout,
                capture.stderr,
                self.max_output_bytes,
            )
        if capture.timed_out:
            raise GitTimeoutError(
                operation,
                argv,
                capture.returncode,
                capture.stdout,
                capture.stderr,
                self.timeout_s,
            )
        if capture.read_error is not None:
            raise GitCommandError(
                operation,
                argv,
                capture.returncode,
                _decode(capture.stderr),
                f"git {operation} output could not be read: {capture.read_error}",
                stdout_bytes=capture.stdout,
                stderr_bytes=capture.stderr,
            ) from capture.read_error
        assert capture.returncode is not None
        return _GitResult(argv, capture.returncode, capture.stdout, capture.stderr)

    @staticmethod
    def _require_success(operation: str, result: _GitResult) -> None:
        if result.returncode == 0:
            return
        stderr = _decode(result.stderr)
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
