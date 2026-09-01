from __future__ import annotations

import os
from typing import Iterable

from .paths import PathInput


def changed_snapshot_paths(workspace: object) -> tuple[str, ...]:
    """Return changed paths without assuming that HEAD exists."""
    head = workspace._invoke(  # type: ignore[attr-defined]
        "changed_snapshot_paths", ("rev-parse", "--verify", "--quiet", "HEAD")
    )
    if head.returncode == 0:
        diff_arguments = (
            "diff", "--name-only", "--no-renames", "-z", "HEAD", "--"
        )
    else:
        if head.returncode != 1:
            workspace._require_success(  # type: ignore[attr-defined]
                "changed_snapshot_paths", head
            )
        diff_arguments = (
            "diff", "--cached", "--name-only", "--no-renames", "-z", "--"
        )
    tracked = workspace._invoke(  # type: ignore[attr-defined]
        "changed_snapshot_paths", diff_arguments
    )
    workspace._require_success(  # type: ignore[attr-defined]
        "changed_snapshot_paths", tracked
    )
    untracked = workspace._invoke(  # type: ignore[attr-defined]
        "changed_snapshot_paths",
        ("ls-files", "-z", "--others", "--exclude-standard"),
    )
    workspace._require_success(  # type: ignore[attr-defined]
        "changed_snapshot_paths", untracked
    )
    from .git import _decode_path_list

    return tuple(
        sorted(
            set(_decode_path_list(tracked.stdout))
            | set(_decode_path_list(untracked.stdout))
        )
    )


def tracked_paths(
    workspace: object, paths: Iterable[PathInput]
) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes, os.PathLike)):
        paths = (paths,)
    relative = tuple(
        workspace.guard.relative_literal(path).as_posix()  # type: ignore[attr-defined]
        for path in paths
    )
    if not relative:
        return ()
    result = workspace._invoke(  # type: ignore[attr-defined]
        "tracked_paths", ("ls-files", "-z", "--cached", "--", *relative)
    )
    workspace._require_success("tracked_paths", result)  # type: ignore[attr-defined]
    from .git import _decode_path_list

    return tuple(sorted(_decode_path_list(result.stdout)))


__all__ = ["changed_snapshot_paths", "tracked_paths"]
