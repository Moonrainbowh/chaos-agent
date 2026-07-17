from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping, Optional

from code_agent.core._json import JSONValue, freeze_mapping


MAX_REWIND_ACTION_PATHS = 32
MAX_REWIND_HANDLE_BYTES = 64 * 1024
MAX_REWIND_TEXT_FIELD = 512
DEFAULT_REWIND_MAX_MUTATIONS = 256
DEFAULT_REWIND_MAX_PATHS = 512
MAX_REWIND_PAGE_SIZE = 100

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RewindCoverageState(str, Enum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"


class RewindMutationStatus(str, Enum):
    PREPARED = "prepared"
    COMPLETED = "completed"
    ABORTED = "aborted"
    GAP = "gap"


class RewindBaseline(str, Enum):
    GIT_STAGED = "git-staged"
    GIT_UNSTAGED = "git-unstaged"
    GIT_UNTRACKED = "git-untracked"
    NON_GIT_EXISTING = "non-git-existing"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > MAX_REWIND_TEXT_FIELD:
        raise ValueError(f"{label} must be at most {MAX_REWIND_TEXT_FIELD} characters")
    return value


def optional_text(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    return required_text(value, label)


def bounded_int(
    value: object, label: str, *, minimum: int = 0, maximum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must not exceed {maximum}")
    return value


def utc_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def sha256(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def canonical_path(value: object) -> str:
    path = required_text(value, "path")
    if (
        "\0" in path
        or "\\" in path
        or path.startswith("/")
        or re.match(r"[A-Za-z]:/", path) is not None
        or path.endswith("/")
        or "//" in path
        or PurePosixPath(path).as_posix() != path
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise ValueError("path must be a canonical relative POSIX path")
    return path


@dataclass(frozen=True)
class CoverageToken:
    workspace_fingerprint: str
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_fingerprint",
            sha256(self.workspace_fingerprint, "workspace_fingerprint"),
        )
        bounded_int(self.generation, "generation", minimum=1)


@dataclass(frozen=True)
class RewindMutationPath:
    path: str
    before_existed: bool
    before_sha256: str | None
    baseline: RewindBaseline
    after_existed: bool
    after_sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonical_path(self.path))
        if not isinstance(self.before_existed, bool):
            raise TypeError("before_existed must be a bool")
        if not isinstance(self.after_existed, bool):
            raise TypeError("after_existed must be a bool")
        before = sha256(self.before_sha256, "before_sha256", optional=True)
        after = sha256(self.after_sha256, "after_sha256", optional=True)
        if self.before_existed != (before is not None):
            raise ValueError("before existence and hash must agree")
        if self.after_existed != (after is not None):
            raise ValueError("after existence and hash must agree")
        if not isinstance(self.baseline, RewindBaseline):
            raise TypeError("baseline must be a RewindBaseline")
        if self.baseline is RewindBaseline.ABSENT and self.before_existed:
            raise ValueError("absent baseline requires an absent preimage")


def validate_coverage(value: object) -> CoverageToken:
    if not isinstance(value, CoverageToken):
        raise TypeError("coverage must be a CoverageToken")
    return value


def validate_identity(record: object) -> None:
    validate_coverage(getattr(record, "coverage"))
    for name in ("owner_thread_id", "origin_thread_id", "request_id", "action_name"):
        object.__setattr__(record, name, required_text(getattr(record, name), name))
    for name in ("task_id", "parent_request_id"):
        object.__setattr__(record, name, optional_text(getattr(record, name), name))


def rewind_paths(
    value: object, *, empty: bool = False
) -> tuple[RewindMutationPath, ...]:
    if type(value) is not tuple:
        raise TypeError("paths must be a tuple")
    result = value
    if not empty and not result:
        raise ValueError("paths must not be empty")
    if len(result) > MAX_REWIND_ACTION_PATHS:
        raise ValueError("too many rewind paths")
    if any(not isinstance(item, RewindMutationPath) for item in result):
        raise TypeError("paths must contain RewindMutationPath values")
    names = [item.path for item in result]
    if len(names) != len(set(names)):
        raise ValueError("paths must be unique")
    return result


def rewind_handle(value: object) -> Mapping[str, JSONValue]:
    from ._rewind_codec import encode_rewind_handle

    encode_rewind_handle(value)
    return freeze_mapping(value, "snapshot_handle")  # type: ignore[arg-type]


@dataclass(frozen=True)
class RewindMutationPrepare:
    coverage: CoverageToken
    owner_thread_id: str
    origin_thread_id: str
    task_id: str | None
    parent_request_id: str | None
    request_id: str
    action_name: str
    snapshot_handle: Mapping[str, JSONValue]
    paths: tuple[RewindMutationPath, ...]

    def __post_init__(self) -> None:
        validate_identity(self)
        object.__setattr__(
            self, "snapshot_handle", rewind_handle(self.snapshot_handle)
        )
        object.__setattr__(self, "paths", rewind_paths(self.paths))


@dataclass(frozen=True)
class RewindGapPrepare:
    coverage: CoverageToken
    owner_thread_id: str
    origin_thread_id: str
    task_id: str | None
    parent_request_id: str | None
    request_id: str
    action_name: str
    reason: str

    def __post_init__(self) -> None:
        validate_identity(self)
        object.__setattr__(self, "reason", required_text(self.reason, "reason"))


@dataclass(frozen=True)
class RewindCoverageRecord:
    token: CoverageToken
    state: RewindCoverageState
    mutation_high_water: int
    invalidation_reason: str | None

    def __post_init__(self) -> None:
        validate_coverage(self.token)
        if not isinstance(self.state, RewindCoverageState):
            raise TypeError("state must be a RewindCoverageState")
        bounded_int(self.mutation_high_water, "mutation_high_water")
        reason = optional_text(self.invalidation_reason, "invalidation_reason")
        if (self.state is RewindCoverageState.ACTIVE) != (reason is None):
            raise ValueError("active coverage must have no invalidation reason")
        object.__setattr__(self, "invalidation_reason", reason)

    @property
    def workspace_fingerprint(self) -> str:
        return self.token.workspace_fingerprint

    @property
    def generation(self) -> int:
        return self.token.generation
