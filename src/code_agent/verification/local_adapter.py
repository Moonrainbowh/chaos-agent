from __future__ import annotations

import json
import shutil
from pathlib import Path

from .models import VerificationCommand, VerificationKind, VerificationRequest, VerificationUnavailable
from .python_adapter import PythonVerificationAdapter


class LocalVerificationAdapter:
    """Resolve only registered Python, Node, and .NET recipes into fixed argv."""

    def __init__(self, workspace_root: Path) -> None:
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        self._root = workspace_root.resolve(strict=False)
        self._python = PythonVerificationAdapter(self._root)

    def build(self, request: VerificationRequest) -> VerificationCommand | VerificationUnavailable:
        if not isinstance(request, VerificationRequest):
            raise TypeError("request must be a VerificationRequest")
        if request.kind.value.startswith("python") or request.kind is VerificationKind.PYTEST:
            return self._python.build(request)
        if request.kind in {VerificationKind.NODE_TEST, VerificationKind.NODE_BUILD, VerificationKind.NODE_LINT}:
            return self._node(request)
        return self._dotnet(request)

    def _node(self, request: VerificationRequest) -> VerificationCommand | VerificationUnavailable:
        if request.targets:
            return VerificationUnavailable(request.kind, "node verification does not accept targets")
        directory = self._resolve(request.cwd)
        manifest = directory / "package.json"
        if not manifest.is_file() or not (directory / "node_modules").is_dir():
            return VerificationUnavailable(request.kind, "package.json or existing node_modules is unavailable")
        try:
            scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError, AttributeError):
            scripts = {}
        script = {
            VerificationKind.NODE_TEST: "test",
            VerificationKind.NODE_BUILD: "build",
            VerificationKind.NODE_LINT: "lint",
        }[request.kind]
        if not isinstance(scripts, dict) or not isinstance(scripts.get(script), str):
            return VerificationUnavailable(request.kind, f"package script '{script}' is unavailable")
        if shutil.which("npm") is None:
            return VerificationUnavailable(request.kind, "npm executable is unavailable")
        return VerificationCommand(("npm", "run", script, "--", "--offline"), request.cwd, request.timeout_s)

    def _dotnet(self, request: VerificationRequest) -> VerificationCommand | VerificationUnavailable:
        if len(request.targets) > 1:
            return VerificationUnavailable(request.kind, "dotnet verification accepts at most one target")
        if shutil.which("dotnet") is None:
            return VerificationUnavailable(request.kind, "dotnet executable is unavailable")
        target = request.targets[0] if request.targets else None
        if target is not None:
            path = self._resolve(target)
            if path.suffix not in {".sln", ".csproj", ".fsproj"}:
                return VerificationUnavailable(request.kind, "dotnet target must be a solution or project")
        else:
            directory = self._resolve(request.cwd)
            if not any(directory.glob("*.sln")) and not any(directory.glob("*.csproj")) and not any(directory.glob("*.fsproj")):
                return VerificationUnavailable(request.kind, "dotnet solution or project is unavailable")
        verb = "test" if request.kind is VerificationKind.DOTNET_TEST else "build"
        argv = ("dotnet", verb, *(request.targets or ()), "--no-restore")
        return VerificationCommand(argv, request.cwd, request.timeout_s)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self._root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("verification paths must remain inside the workspace") from error
        return candidate
