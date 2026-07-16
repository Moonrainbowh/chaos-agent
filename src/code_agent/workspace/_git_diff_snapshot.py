from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Callable, Protocol

from ._guarded_read import read_guarded_file
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
        return self.remaining


def decode_guarded_paths(
    raw: bytes,
    guard: WorkspacePathGuard,
    budget: SnapshotBudget,
) -> tuple[str, ...]:
    """Decode, re-guard, and sort Git's NUL-delimited paths."""
    if raw and not raw.endswith(b"\0"):
        raise WorkspaceError("git returned a malformed path list")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceError("git returned non-UTF-8 paths") from error
    entries = decoded.split("\0")[:-1] if decoded else ()
    paths: list[str] = []
    for supplied in entries:
        if not supplied:
            raise WorkspaceError("git returned an empty path")
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
        data = read_guarded_file(path, guard, budget.remaining)
        if len(data) > budget.remaining:
            raise SnapshotBudgetExceeded
        budget.consume(len(data))
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
    staged_paths = _tracked_paths(
        invoke, require_success, guard, budget, "diff_staged_paths", True, paths
    )
    unstaged_paths = _tracked_paths(
        invoke, require_success, guard, budget, "diff_unstaged_paths", False, paths
    )
    staged = _tracked_patch(
        invoke, require_success, budget, "diff_staged", True, staged_paths
    )
    unstaged = _tracked_patch(
        invoke, require_success, budget, "diff_unstaged", False, unstaged_paths
    )
    raw_paths = _command(
        invoke, require_success, "untracked_paths",
        ("ls-files", "--others", "--exclude-standard", "-z", "--", *paths), budget,
    )
    untracked_paths = decode_guarded_paths(raw_paths, guard, budget)
    untracked = render_untracked_diff(untracked_paths, guard, budget)
    return staged, unstaged, untracked, untracked_paths


def _tracked_paths(
    invoke: Callable[[str, tuple[str, ...], int | None], GitResult],
    require_success: Callable[[str, GitResult], None],
    guard: WorkspacePathGuard,
    budget: SnapshotBudget,
    operation: str,
    cached: bool,
    filters: tuple[str, ...],
) -> tuple[str, ...]:
    cached_option = ("--cached",) if cached else ()
    arguments = (
        "diff", "--no-ext-diff", "--no-textconv", *cached_option,
        "--name-only", "-z", "--no-renames", "--",
    )
    raw = _command(invoke, require_success, operation, arguments, budget)
    return _filter_paths(decode_guarded_paths(raw, guard, budget), filters)


def _tracked_patch(
    invoke: Callable[[str, tuple[str, ...], int | None], GitResult],
    require_success: Callable[[str, GitResult], None],
    budget: SnapshotBudget,
    operation: str,
    cached: bool,
    paths: tuple[str, ...],
) -> bytes:
    if not paths:
        return b""
    cached_option = ("--cached",) if cached else ()
    arguments = (
        "diff", "--no-ext-diff", "--no-textconv", *cached_option,
        "--no-renames", "--", *paths,
    )
    return _command(invoke, require_success, operation, arguments, budget)


def _filter_paths(changed: tuple[str, ...], filters: tuple[str, ...]) -> tuple[str, ...]:
    if not filters or any(item in ("", ".") for item in filters):
        return changed
    prefixes = tuple(item.rstrip("/") + "/" for item in filters)
    return tuple(
        path
        for path in changed
        if path in filters or any(path.startswith(prefix) for prefix in prefixes)
    )


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


def _render_file(path: str, data: bytes) -> str:
    display = _header_path(path)
    if b"\0" in data:
        return _binary_marker(display)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _binary_marker(display)
    header = f"diff --git a/{display} b/{display}\nnew file mode 100644\n"
    if not data:
        return header + "index 0000000..e69de29\n"
    lines = text.splitlines(keepends=True)
    rendered = "".join(
        difflib.unified_diff(
            (), lines, fromfile="/dev/null", tofile=f"b/{display}", lineterm="\n"
        )
    )
    if not data.endswith(b"\n"):
        rendered += "\n\\ No newline at end of file\n"
    return header + rendered


def _binary_marker(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
        f"Binary files /dev/null and b/{path} differ\n"
    )


def _header_path(path: str) -> str:
    return (
        path.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
