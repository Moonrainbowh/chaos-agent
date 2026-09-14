from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence

from ._git_errors import GitTimeoutError
from .errors import SearchTimeoutError, WindowsLongPathError, WorkspaceError, WorkspaceScanLimitError
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


def iter_search_inventory(
    guard: WorkspacePathGuard,
    ignore: IgnoreRules,
    provider: Callable[..., Sequence[str]],
    root: str | None,
    limit: int,
    remaining: Callable[[], float],
) -> Iterator[str]:
    """Use a Host-owned inventory within the same search deadline and guard."""
    target = guard.root if root is None else guard.resolve(root)
    if not target.exists():
        raise WorkspaceError("search root must be an existing file or directory")
    scope = target.relative_to(guard.root).as_posix()
    scope = "" if scope == "." else scope
    try:
        candidates = provider(timeout_s=remaining())
    except GitTimeoutError as error:
        raise SearchTimeoutError("search inventory deadline exceeded") from error
    remaining()
    for scanned, candidate in enumerate(sorted(set(candidates)), 1):
        remaining()
        if scanned > limit:
            raise WorkspaceScanLimitError("search inventory entry limit exceeded")
        normalized = candidate.replace("\\", "/")
        if scope and not (normalized.casefold() == scope.casefold()
                          or normalized.casefold().startswith(scope.casefold() + "/")):
            continue
        try:
            path = guard.resolve(candidate)
            relative = path.relative_to(guard.root).as_posix()
            if path.is_file() and not ignore.is_ignored(relative):
                yield relative
        except WindowsLongPathError:
            raise
        except (OSError, WorkspaceError):
            continue
