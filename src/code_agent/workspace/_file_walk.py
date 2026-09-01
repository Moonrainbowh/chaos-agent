from __future__ import annotations

import heapq
import itertools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .errors import (
    WindowsLongPathError,
    WorkspaceError,
    WorkspaceScanLimitError,
)
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


def iter_workspace_files(
    guard: WorkspacePathGuard,
    ignore: IgnoreRules,
    max_scanned_entries: int,
    check: Callable[[], None] | None = None,
) -> Iterator[str]:
    """Yield guarded files in global relative-path order."""
    pending: list[tuple[str, int, Path, Path]] = []
    sequence = itertools.count()
    visited: set[tuple[int, int]] = set()
    budget = _ScanBudget(max_scanned_entries)
    _enqueue_directory(
        guard.root,
        Path(),
        guard,
        ignore,
        pending,
        sequence,
        visited,
        budget,
        check,
    )

    while pending:
        relative, _, resolved, logical = heapq.heappop(pending)
        try:
            if resolved.is_dir():
                _enqueue_directory(
                    resolved,
                    logical,
                    guard,
                    ignore,
                    pending,
                    sequence,
                    visited,
                    budget,
                    check,
                )
            elif resolved.is_file() and not ignore.is_ignored(relative):
                yield relative
        except OSError:
            continue


def _enqueue_directory(
    directory: Path,
    logical_parent: Path,
    guard: WorkspacePathGuard,
    ignore: IgnoreRules,
    pending: list[tuple[str, int, Path, Path]],
    sequence: Iterator[int],
    visited: set[tuple[int, int]],
    budget: "_ScanBudget",
    check: Callable[[], None] | None,
) -> None:
    try:
        stat_result = directory.stat()
        identity = (stat_result.st_dev, stat_result.st_ino)
        if identity in visited:
            return
        visited.add(identity)
        with os.scandir(directory) as entries:
            for entry in entries:
                if check is not None:
                    check()
                budget.consume()
                logical = logical_parent / entry.name
                relative = logical.as_posix()
                if ignore.is_builtin_ignored(relative):
                    continue
                try:
                    resolved = guard.resolve(entry.path)
                except WindowsLongPathError:
                    raise
                except (OSError, WorkspaceError):
                    continue
                heapq.heappush(
                    pending,
                    (relative, next(sequence), resolved, logical),
                )
    except OSError:
        return


@dataclass
class _ScanBudget:
    limit: int
    scanned: int = 0

    def consume(self) -> None:
        self.scanned += 1
        if self.scanned > self.limit:
            raise WorkspaceScanLimitError(
                f"workspace scan exceeds {self.limit} entries"
            )
