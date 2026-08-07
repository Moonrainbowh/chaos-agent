from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

from .paths import contained_path, ensure_no_link_parent, is_link_or_reparse


def materialize_fixture_files(workspace: Path, files: Mapping[str, str]) -> None:
    for relative_path, content in files.items():
        path = contained_path(workspace, relative_path, "fixture path")
        ensure_no_link_parent(workspace, path, "fixture path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def outside_workspace_paths(temporary: Path, workspace: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for path in temporary.rglob("*"):
        if path == workspace or workspace in path.parents:
            continue
        paths.append(path.relative_to(temporary).as_posix())
    return tuple(sorted(paths))


def workspace_links(workspace: Path) -> tuple[str, ...]:
    return tuple(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if is_link_or_reparse(path)
    )


def remove_tree(path: Path) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists()
