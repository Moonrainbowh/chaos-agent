from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


class VerificationKind(str, Enum):
    PYTHON_UNITTEST = "python_unittest"
    PYTEST = "pytest"
    PYTHON_COMPILEALL = "python_compileall"
    PYTHON_BUILD = "python_build"
    NODE_TEST = "node_test"
    NODE_BUILD = "node_build"
    NODE_LINT = "node_lint"
    DOTNET_TEST = "dotnet_test"
    DOTNET_BUILD = "dotnet_build"


def _relative_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank relative text")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError(f"{name} must be a workspace-relative POSIX path")
    return path.as_posix()


@dataclass(frozen=True)
class VerificationRequest:
    kind: VerificationKind
    cwd: str = "."
    targets: tuple[str, ...] = ()
    timeout_s: int = 300

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VerificationKind):
            raise TypeError("kind must be a VerificationKind")
        object.__setattr__(self, "cwd", _relative_path(self.cwd, "cwd"))
        targets = tuple(_relative_path(item, "target") for item in self.targets)
        if len(targets) > 16 or len(set(targets)) != len(targets):
            raise ValueError("targets must contain at most 16 unique paths")
        object.__setattr__(self, "targets", targets)
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, int):
            raise TypeError("timeout_s must be an integer")
        if not 1 <= self.timeout_s <= 900:
            raise ValueError("timeout_s must be between 1 and 900")


@dataclass(frozen=True)
class VerificationCommand:
    argv: tuple[str, ...]
    cwd: str
    timeout_s: int

    def __post_init__(self) -> None:
        if not self.argv or not all(isinstance(item, str) and item for item in self.argv):
            raise ValueError("argv must contain non-blank strings")
        object.__setattr__(self, "cwd", _relative_path(self.cwd, "cwd"))
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, int) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")


@dataclass(frozen=True)
class VerificationUnavailable:
    kind: VerificationKind
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VerificationKind):
            raise TypeError("kind must be a VerificationKind")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-blank text")
