from __future__ import annotations

import copy
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Optional


class RuntimeKind(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


class StreamName(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


class TerminationReason(str, Enum):
    EXITED = "exited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OUTPUT_LIMIT = "output_limit"


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return converted


def _command_argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("argv must be a tuple")
    if not value:
        raise ValueError("argv must not be empty")
    if not all(isinstance(item, str) for item in value):
        raise TypeError("argv must contain only strings")
    if not value[0]:
        raise ValueError("argv executable must not be empty")
    return tuple(value)


def _immutable_environment(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("explicit_env must be a mapping")
    copied: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str):
            raise TypeError("explicit_env names and values must be strings")
        copied[copy.deepcopy(name)] = copy.deepcopy(item)
    return MappingProxyType(copied)


def _immutable_bytes(value: object, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(value)


@dataclass(frozen=True)
class CommandSpec:
    cwd: Path
    argv: Optional[tuple[str, ...]] = None
    powershell_script: Optional[str] = None
    timeout_s: float = 60.0
    max_output_bytes: int = 1_000_000
    explicit_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        raw_cwd = os.fspath(self.cwd)
        if not isinstance(raw_cwd, str):
            raise TypeError("cwd must be a text path")
        object.__setattr__(self, "cwd", Path(copy.deepcopy(raw_cwd)))

        has_argv = self.argv is not None
        has_script = self.powershell_script is not None
        if has_argv == has_script:
            raise ValueError("exactly one of argv or powershell_script is required")
        if has_argv:
            object.__setattr__(self, "argv", _command_argv(self.argv))
        elif not isinstance(self.powershell_script, str):
            raise TypeError("powershell_script must be a string")
        else:
            object.__setattr__(
                self, "powershell_script", copy.deepcopy(self.powershell_script)
            )

        object.__setattr__(self, "timeout_s", _positive_float(self.timeout_s, "timeout_s"))
        if isinstance(self.max_output_bytes, bool) or not isinstance(
            self.max_output_bytes, int
        ):
            raise TypeError("max_output_bytes must be an integer")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        object.__setattr__(
            self, "explicit_env", _immutable_environment(self.explicit_env)
        )


@dataclass(frozen=True)
class OutputChunk:
    stream: StreamName
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.stream, StreamName):
            raise TypeError("stream must be a StreamName")
        object.__setattr__(self, "data", _immutable_bytes(self.data, "data"))


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    display_command: str
    returncode: Optional[int]
    reason: TerminationReason
    stdout: bytes
    stderr: bytes
    duration_s: float
    truncated: bool
    cwd: str
    cancellation_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", _command_argv(self.argv))
        if not isinstance(self.display_command, str):
            raise TypeError("display_command must be a string")
        if self.returncode is not None and (
            isinstance(self.returncode, bool) or not isinstance(self.returncode, int)
        ):
            raise TypeError("returncode must be an integer or None")
        if not isinstance(self.reason, TerminationReason):
            raise TypeError("reason must be a TerminationReason")
        object.__setattr__(self, "stdout", _immutable_bytes(self.stdout, "stdout"))
        object.__setattr__(self, "stderr", _immutable_bytes(self.stderr, "stderr"))
        if isinstance(self.duration_s, bool) or not isinstance(
            self.duration_s, (int, float)
        ):
            raise TypeError("duration_s must be a number")
        duration = float(self.duration_s)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("duration_s must be non-negative and finite")
        object.__setattr__(self, "duration_s", duration)
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a bool")
        if not isinstance(self.cwd, str):
            raise TypeError("cwd must be a string")
        if not self.cwd.strip():
            raise ValueError("cwd must not be blank")
        relative_cwd = PurePosixPath(self.cwd)
        if (
            "\\" in self.cwd
            or relative_cwd.is_absolute()
            or ".." in relative_cwd.parts
            or relative_cwd.as_posix() != self.cwd
        ):
            raise ValueError("cwd must be a canonical workspace-relative POSIX path")
        object.__setattr__(self, "cwd", copy.deepcopy(self.cwd))
        if self.cancellation_reason is not None:
            if not isinstance(self.cancellation_reason, str):
                raise TypeError("cancellation_reason must be a string or None")
            if not self.cancellation_reason.strip():
                raise ValueError("cancellation_reason must not be blank")
            object.__setattr__(
                self,
                "cancellation_reason",
                copy.deepcopy(self.cancellation_reason),
            )
        if self.reason is TerminationReason.EXITED and self.returncode is None:
            raise ValueError("exited results require an integer returncode")
        if (self.reason is TerminationReason.CANCELLED) != (
            self.cancellation_reason is not None
        ):
            raise ValueError("cancellation_reason is required only for cancellation")
        if (self.reason is TerminationReason.OUTPUT_LIMIT) != self.truncated:
            raise ValueError("truncated must identify output-limit termination")
        if self.reason in {
            TerminationReason.TIMEOUT,
            TerminationReason.CANCELLED,
        } and self.returncode == 0:
            raise ValueError("interrupted results cannot report returncode zero")
