from __future__ import annotations

from collections.abc import Iterable

from .errors import WorkspaceError, WorkspaceScanLimitError
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


def known_workspace_files(
    candidates: Iterable[str],
    guard: WorkspacePathGuard,
    ignore: IgnoreRules,
    *,
    max_entries: int,
    max_scanned_entries: int,
) -> tuple[str, ...]:
    """Filter a trusted candidate inventory through workspace visibility rules."""
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries <= 0:
        raise ValueError("max_entries must be a positive integer")
    if (
        not isinstance(max_scanned_entries, int)
        or isinstance(max_scanned_entries, bool)
        or max_scanned_entries <= 0
    ):
        raise ValueError("max_scanned_entries must be a positive integer")
    visible: list[str] = []
    for scanned, candidate in enumerate(candidates, start=1):
        if scanned > max_scanned_entries:
            raise WorkspaceScanLimitError(
                f"workspace scan exceeds {max_scanned_entries} entries"
            )
        if not isinstance(candidate, str):
            raise TypeError("candidate paths must be strings")
        try:
            resolved = guard.resolve(candidate)
            relative = guard.relative(resolved).as_posix()
            if resolved.is_file() and not ignore.is_ignored(relative):
                visible.append(relative)
        except (OSError, WorkspaceError):
            continue
        if len(visible) >= max_entries:
            break
    return tuple(sorted(visible))
