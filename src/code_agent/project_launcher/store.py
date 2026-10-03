"""User-selected project roots, independent of the invocation working directory."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable

from .directory import browse_roots, child_directories

MAX_PROJECTS = 64
MAX_STATE_BYTES = 1_048_576


class ProjectStoreError(ValueError):
    """An invalid project or state file must not silently choose another root."""


@dataclass(frozen=True)
class ProjectEntry:
    identifier: str
    root: Path
    name: str
    available: bool


def _root(value: Path, *, available: bool = False) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ProjectStoreError("项目目录必须是绝对路径")
    try:
        root = value.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ProjectStoreError("无法解析项目目录") from error
    if available and not root.is_dir():
        raise ProjectStoreError("项目目录不存在或不是文件夹")
    return root


def _identity(root: Path) -> str:
    return os.path.normcase(str(root))


def _entry(root: Path) -> ProjectEntry:
    return ProjectEntry(hashlib.sha256(_identity(root).encode("utf-8")).hexdigest()[:24],
                        root, root.name or str(root), root.is_dir())


class ProjectStore:
    """Atomic UTF-8 directory registry; callers schedule blocking IO off the UI loop."""

    def __init__(self, path: Path):
        if not isinstance(path, Path) or not path.is_absolute():
            raise ProjectStoreError("项目入口存储必须是固定绝对路径")
        self.path = path.resolve(strict=False)

    def _read(self) -> dict:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            return {"version": 1, "projects": [], "recent": None, "removed": []}
        except OSError as error:
            raise ProjectStoreError("无法读取项目入口存储") from error
        try:
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError("oversized state")
            data = json.loads(raw.decode("utf-8"))
            if (not isinstance(data, dict) or type(data.get("version")) is not int
                    or data.get("version") != 1):
                raise ValueError("unsupported project state")
            projects, removed, recent = data["projects"], data["removed"], data["recent"]
            if (not isinstance(projects, list) or len(projects) > MAX_PROJECTS
                    or not isinstance(removed, list) or len(removed) > 4096
                    or not all(isinstance(value, str) for value in projects + removed)
                    or (recent is not None and not isinstance(recent, str))):
                raise ValueError("invalid project state")
            roots = [_root(Path(value)) for value in projects]
            tombstones = [_root(Path(value)) for value in removed]
            identities = [_identity(root) for root in roots]
            if len(set(identities)) != len(identities):
                raise ValueError("duplicate project state")
            if recent is not None and _identity(_root(Path(recent))) not in identities:
                raise ValueError("recent root is not registered")
            return {"version": 1, "projects": [str(root) for root in roots],
                    "recent": str(_root(Path(recent))) if recent else None,
                    "removed": [str(root) for root in tombstones]}
        except (UnicodeError, ValueError, KeyError, TypeError, OSError) as error:
            raise ProjectStoreError("项目入口存储损坏；原文件已保留") from error

    def _write(self, data: dict) -> None:
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise ProjectStoreError("项目入口存储超出容量；原文件已保留")
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".projects-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise ProjectStoreError("无法保存项目入口；原文件已保留") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def entries(self) -> tuple[ProjectEntry, ...]:
        data = self._read()
        roots = [Path(value) for value in data["projects"]]
        recent = data["recent"]
        if recent:
            roots.sort(key=lambda root: _identity(root) != _identity(Path(recent)))
        return tuple(_entry(root) for root in roots)

    def add(self, root: Path) -> ProjectEntry:
        root = _root(root, available=True)
        data = self._read()
        identity = _identity(root)
        if not any(_identity(Path(value)) == identity for value in data["projects"]):
            if len(data["projects"]) >= MAX_PROJECTS:
                raise ProjectStoreError("最多保留64个项目入口，请先移除一个")
            data["projects"].append(str(root))
        data["removed"] = [value for value in data["removed"] if _identity(Path(value)) != identity]
        self._write(data)
        return _entry(root)

    def select(self, root: Path) -> Path:
        root = _root(root, available=True)
        data = self._read()
        if not any(_identity(Path(value)) == _identity(root) for value in data["projects"]):
            raise ProjectStoreError("请先添加项目入口")
        data["recent"] = str(root)
        self._write(data)
        return root

    def remove(self, root: Path) -> None:
        root = _root(root)
        data = self._read()
        identity = _identity(root)
        data["projects"] = [value for value in data["projects"] if _identity(Path(value)) != identity]
        if data["recent"] and _identity(Path(data["recent"])) == identity:
            data["recent"] = None
        if not any(_identity(Path(value)) == identity for value in data["removed"]):
            if len(data["removed"]) >= 4096:
                raise ProjectStoreError("移除记录超出容量；原文件已保留")
            data["removed"].append(str(root))
        self._write(data)

    def seed(self, roots: Iterable[Path]) -> None:
        data = self._read()
        known = {_identity(Path(value)) for value in data["projects"] + data["removed"]}
        changed = False
        for value in roots:
            if len(data["projects"]) >= MAX_PROJECTS:
                break
            try:
                root = _root(value, available=True)
            except (ProjectStoreError, OSError):
                continue
            if _identity(root) not in known:
                data["projects"].append(str(root))
                known.add(_identity(root))
                changed = True
        if changed:
            self._write(data)

    def last_root(self) -> Path | None:
        recent = self._read()["recent"]
        return Path(recent) if recent is not None else None

    def browse_roots(self) -> tuple[Path, ...]:
        return browse_roots(tuple(entry.root for entry in self.entries()))

    def child_directories(self, root: Path) -> tuple[Path, ...]:
        return child_directories(_root(root, available=True))
