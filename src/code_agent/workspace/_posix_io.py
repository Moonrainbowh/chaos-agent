from __future__ import annotations

import os
from pathlib import Path


O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def open_directory(path: Path) -> int:
    return os.open(path, os.O_RDONLY | _O_DIRECTORY | O_NOFOLLOW)


def open_directory_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | _O_DIRECTORY | O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


def open_read(parent_fd: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | O_NOFOLLOW, dir_fd=parent_fd)


def create_temp(parent_fd: int, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW
    return os.open(name, flags, 0o600, dir_fd=parent_fd)


def mkdir(parent_fd: int, name: str) -> None:
    os.mkdir(name, mode=0o777, dir_fd=parent_fd)


def replace(parent_fd: int, source: str, target: str) -> None:
    os.replace(source, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)


def unlink(parent_fd: int, name: str) -> None:
    os.unlink(name, dir_fd=parent_fd)


def rmdir(parent_fd: int, name: str) -> None:
    os.rmdir(name, dir_fd=parent_fd)


def inspect(parent_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
