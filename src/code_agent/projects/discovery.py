from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from code_agent.workspace.paths import WorkspacePathGuard


class ProjectKind(str, Enum):
    PYTHON = "python"
    NODE = "node"
    DOTNET = "dotnet"


@dataclass(frozen=True)
class VerificationRecipe:
    label: str
    argv: tuple[str, ...]
    cwd: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class ProjectCandidate:
    kind: ProjectKind
    root: str
    provenance: str
    recipes: tuple[VerificationRecipe, ...]


def discover_projects(root: Path, guard: WorkspacePathGuard, executable_lookup: Callable[[str], str | None] = shutil.which, *, max_roots: int = 16) -> tuple[ProjectCandidate, ...]:
    if not isinstance(root, Path) or not isinstance(guard, WorkspacePathGuard) or not callable(executable_lookup):
        raise TypeError("root, guard, and executable_lookup must be valid")
    if isinstance(max_roots, bool) or not isinstance(max_roots, int) or not 1 <= max_roots <= 16:
        raise ValueError("max_roots must be between 1 and 16")
    workspace = guard.resolve(root)
    manifests = _find_manifests(workspace, max_roots)
    candidates: list[ProjectCandidate] = []
    for directory, names in manifests:
        relative = guard.relative(directory).as_posix()
        if any(name in {"pyproject.toml", "pytest.ini", "tox.ini"} for name in names):
            candidates.append(_python_candidate(directory, relative, executable_lookup))
        if "package.json" in names:
            candidates.append(_node_candidate(directory, relative, executable_lookup))
        if any(name.endswith((".sln", ".csproj", ".fsproj")) for name in names):
            candidates.append(_dotnet_candidate(directory, relative, executable_lookup))
    return tuple(sorted(candidates, key=lambda candidate: (candidate.root, candidate.kind.value)))


def _find_manifests(root: Path, maximum: int) -> list[tuple[Path, set[str]]]:
    found: dict[Path, set[str]] = {}
    for path in root.rglob("*"):
        if len(found) >= maximum and path.parent not in found:
            break
        if not path.is_file() or any(part in {".git", "node_modules", ".chaos-agent", ".code-agent"} for part in path.relative_to(root).parts):
            continue
        if path.name in {"pyproject.toml", "pytest.ini", "tox.ini", "package.json"} or path.suffix in {".sln", ".csproj", ".fsproj"}:
            found.setdefault(path.parent, set()).add(path.name)
    return list(found.items())


def _recipe(label: str, argv: tuple[str, ...], cwd: str, available: bool, reason: str | None = None) -> VerificationRecipe:
    return VerificationRecipe(label, argv, cwd, available, reason)


def _python_candidate(directory: Path, root: str, lookup: Callable[[str], str | None]) -> ProjectCandidate:
    pytest_available = lookup("pytest") is not None
    build_available = lookup("python") is not None
    recipes = (
        _recipe("python unittest", (sys.executable, "-m", "unittest", "discover"), root, True),
        _recipe("pytest", (sys.executable, "-m", "pytest"), root, pytest_available, None if pytest_available else "pytest executable is unavailable"),
        _recipe("python build", (sys.executable, "-m", "build", "--no-isolation"), root, build_available, None if build_available else "python executable is unavailable"),
    )
    return ProjectCandidate(ProjectKind.PYTHON, root, str(directory / "pyproject.toml"), recipes)


def _node_candidate(directory: Path, root: str, lookup: Callable[[str], str | None]) -> ProjectCandidate:
    try:
        scripts = json.loads((directory / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError, AttributeError):
        scripts = {}
    node = lookup("node")
    local_modules = (directory / "node_modules").is_dir()
    recipes = tuple(_recipe(f"npm {name}", ("npm", "run", name, "--", "--offline"), root, bool(node and local_modules), None if node and local_modules else "node or existing node_modules is unavailable") for name in ("test", "build", "lint") if isinstance(scripts, dict) and isinstance(scripts.get(name), str))
    return ProjectCandidate(ProjectKind.NODE, root, str(directory / "package.json"), recipes)


def _dotnet_candidate(directory: Path, root: str, lookup: Callable[[str], str | None]) -> ProjectCandidate:
    available = lookup("dotnet") is not None
    recipes = (_recipe("dotnet test", ("dotnet", "test", "--no-restore"), root, available, None if available else "dotnet executable is unavailable"), _recipe("dotnet build", ("dotnet", "build", "--no-restore"), root, available, None if available else "dotnet executable is unavailable"))
    return ProjectCandidate(ProjectKind.DOTNET, root, str(directory), recipes)
