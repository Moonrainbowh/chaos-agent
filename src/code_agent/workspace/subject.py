from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .paths import WorkspacePathGuard


_MANIFESTS = frozenset({"pyproject.toml", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "global.json"})


@dataclass(frozen=True)
class SubjectFile:
    path: str
    content_hash: str
    size: int


@dataclass(frozen=True)
class SubjectSnapshot:
    generation: int
    subject_hash: str
    files: tuple[SubjectFile, ...]


def snapshot_subject(guard: WorkspacePathGuard, generation: int, changed_paths: Iterable[str], *, max_files: int = 128, max_bytes: int = 2_000_000) -> SubjectSnapshot:
    if not isinstance(guard, WorkspacePathGuard):
        raise TypeError("guard must be a WorkspacePathGuard")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("generation must be non-negative")
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1 or isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("snapshot limits must be positive integers")
    requested = set(changed_paths)
    if not all(isinstance(path, str) and path for path in requested):
        raise ValueError("changed paths must be non-blank text")
    requested.update(path.name for path in guard.root.iterdir() if path.name in _MANIFESTS)
    if len(requested) > max_files:
        raise ValueError("snapshot file limit exceeded")
    total = 0
    files: list[SubjectFile] = []
    for raw in sorted(requested):
        path = guard.resolve(raw)
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > max_bytes:
            raise ValueError("snapshot byte limit exceeded")
        relative = guard.relative(path).as_posix()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(SubjectFile(relative, content_hash, size))
    digest = hashlib.sha256()
    digest.update(f"generation:{generation}\n".encode())
    for file in files:
        digest.update(f"{file.path}\0{file.content_hash}\0{file.size}\n".encode())
    return SubjectSnapshot(generation, digest.hexdigest(), tuple(files))
