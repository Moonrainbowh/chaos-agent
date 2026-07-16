from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .errors import WorkspaceError
from .paths import WorkspacePathGuard


class SnapshotBudgetExceeded(Exception):
    """Raised without retaining bytes when snapshot work exceeds its budget."""


class GitResult(Protocol):
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass
class SnapshotBudget:
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def consume(self, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("byte_count must not be negative")
        if byte_count > self.remaining:
            raise SnapshotBudgetExceeded
        self.used += byte_count

    def command_limit(self) -> int:
        if self.remaining <= 0:
            raise SnapshotBudgetExceeded
        return self.remaining


def decode_untracked_paths(
    raw: bytes,
    guard: WorkspacePathGuard,
    budget: SnapshotBudget,
) -> tuple[str, ...]:
    """Decode, re-guard, and sort Git's NUL-delimited untracked paths."""
    if raw and not raw.endswith(b"\0"):
        raise WorkspaceError("git returned a malformed untracked path list")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceError("git returned non-UTF-8 untracked paths") from error
    entries = decoded.split("\0")[:-1] if decoded else ()
    paths: list[str] = []
    for supplied in entries:
        if not supplied:
            raise WorkspaceError("git returned an empty untracked path")
        relative = guard.relative(supplied).as_posix()
        budget.consume(len(relative.encode("utf-8")) + 1)
        paths.append(relative)
    return tuple(sorted(dict.fromkeys(paths)))


def render_untracked_diff(
    paths: tuple[str, ...],
    guard: WorkspacePathGuard,
    budget: SnapshotBudget,
) -> str:
    """Render bounded untracked text or metadata-only binary markers."""
    rendered: list[str] = []
    for path in paths:
        absolute = guard.resolve(path)
        data = _read_bounded(absolute, budget)
        facet = _render_file(path, data)
        budget.consume(len(facet.encode("utf-8")))
        rendered.append(facet)
    return "".join(rendered)


def collect_diff_facets(
    invoke: Callable[[str, tuple[str, ...], int | None], GitResult],
    require_success: Callable[[str, GitResult], None],
    guard: WorkspacePathGuard,
    budget: SnapshotBudget,
    paths: tuple[str, ...],
) -> tuple[bytes, bytes, str, tuple[str, ...]]:
    staged = _command(
        invoke, require_success, "diff_staged",
        ("diff", "--no-ext-diff", "--no-textconv", "--cached", "--", *paths), budget,
    )
    unstaged = _command(
        invoke, require_success, "diff_unstaged",
        ("diff", "--no-ext-diff", "--no-textconv", "--", *paths), budget,
    )
    raw_paths = _command(
        invoke, require_success, "untracked_paths",
        ("ls-files", "--others", "--exclude-standard", "-z", "--", *paths), budget,
    )
    untracked_paths = decode_untracked_paths(raw_paths, guard, budget)
    untracked = render_untracked_diff(untracked_paths, guard, budget)
    return staged, unstaged, untracked, untracked_paths


def _command(
    invoke: Callable[[str, tuple[str, ...], int | None], GitResult],
    require_success: Callable[[str, GitResult], None],
    operation: str,
    arguments: tuple[str, ...],
    budget: SnapshotBudget,
) -> bytes:
    result = invoke(operation, arguments, budget.command_limit())
    require_success(operation, result)
    budget.consume(len(result.stdout) + len(result.stderr))
    return result.stdout


def _read_bounded(path: Path, budget: SnapshotBudget) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(budget.remaining + 1)
    except OSError as error:
        raise WorkspaceError(f"cannot read untracked file: {path.name}") from error
    if len(data) > budget.remaining:
        raise SnapshotBudgetExceeded
    budget.consume(len(data))
    return data


def _render_file(path: str, data: bytes) -> str:
    display = _header_path(path)
    if b"\0" in data:
        return _binary_marker(display)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _binary_marker(display)
    lines = text.splitlines(keepends=True)
    rendered = "".join(
        difflib.unified_diff(
            (), lines, fromfile="/dev/null", tofile=f"b/{display}", lineterm="\n"
        )
    )
    if not rendered:
        return f"--- /dev/null\n+++ b/{display}\n"
    return rendered if rendered.endswith("\n") else rendered + "\n"


def _binary_marker(path: str) -> str:
    return (
        f"--- /dev/null\n+++ b/{path}\n"
        f"Binary files /dev/null and b/{path} differ\n"
    )


def _header_path(path: str) -> str:
    return (
        path.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
