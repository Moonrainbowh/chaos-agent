from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .models import VerificationCommand, VerificationKind, VerificationRequest, VerificationUnavailable


class PythonVerificationAdapter:
    """Build fixed local Python verification argv without executing processes."""

    def __init__(self, workspace_root: Path, *, executable: str | None = None) -> None:
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        self._root = workspace_root.resolve(strict=False)
        self._executable = executable or sys.executable
        if not isinstance(self._executable, str) or not self._executable:
            raise ValueError("executable must be non-blank text")

    def build(self, request: VerificationRequest) -> VerificationCommand | VerificationUnavailable:
        if not isinstance(request, VerificationRequest):
            raise TypeError("request must be a VerificationRequest")
        self._resolve(request.cwd)
        for target in request.targets:
            self._resolve(target)
        if request.kind is VerificationKind.PYTHON_BUILD:
            if request.targets:
                return VerificationUnavailable(request.kind, "python_build does not accept targets")
            if importlib.util.find_spec("build") is None:
                return VerificationUnavailable(request.kind, "python build module is unavailable")
            argv = (self._executable, "-m", "build", "--no-isolation")
        elif request.kind is VerificationKind.PYTHON_UNITTEST:
            target = request.targets[0] if request.targets else "."
            if len(request.targets) > 1:
                return VerificationUnavailable(request.kind, "unittest accepts one discovery target")
            argv = (self._executable, "-m", "unittest", "discover", "-s", target)
        elif request.kind is VerificationKind.PYTEST:
            if importlib.util.find_spec("pytest") is None:
                return VerificationUnavailable(request.kind, "pytest module is unavailable")
            argv = (self._executable, "-m", "pytest", *(request.targets or (".",)))
        else:
            argv = (self._executable, "-m", "compileall", *(request.targets or (".",)))
        return VerificationCommand(argv, request.cwd, request.timeout_s)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self._root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("verification paths must remain inside the workspace") from error
        return candidate
