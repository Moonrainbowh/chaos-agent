# Rewind Sessions Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Sessions-only durable rewind ledger, strict immutable records, schema-v11 migration, atomic mutation lifecycle, anchored checkpoints, and bounded read APIs without changing any other Feature or integration file.

**Architecture:** Keep the 373-line legacy `SQLiteSessionRepository` and its checkpoint mixin unchanged. Add a narrow `RewindSessionRepository` whose focused mixins own mutation writes, anchored checkpoint writes, and bounded reads; strict model/row modules isolate trust validation, while v11 creates only empty rewind tables and never synthesizes legacy code facts.

**Tech Stack:** Python 3.10, stdlib `dataclasses`, `enum`, `json`, `base64`, `sqlite3`, `asyncio`, and `unittest`.

---

## File responsibility map

All implementation and test changes stay under `src/code_agent/sessions/**`.

| File | Action | Single responsibility | Expected final lines |
|---|---|---|---:|
| `src/code_agent/sessions/_rewind_model_base.py` | Create | Primitive validation, enums, coverage tokens, paths, and prepare requests | ≤ 250 |
| `src/code_agent/sessions/_rewind_model_records.py` | Create | Frozen mutation/checkpoint/observation/candidate records | ≤ 230 |
| `src/code_agent/sessions/rewind_models.py` | Create | Stable public re-export facade for rewind models | ≤ 55 |
| `src/code_agent/sessions/_rewind_codec.py` | Create | Canonical bounded snapshot-handle JSON and opaque candidate cursor codec | ≤ 95 |
| `src/code_agent/sessions/_rewind_schema.py` | Create | Schema-v11 SQL and required-column declarations | ≤ 125 |
| `src/code_agent/sessions/_rewind_rows.py` | Create | Strict SQLite-row-to-model decoding shared by both mixins | ≤ 155 |
| `src/code_agent/sessions/_rewind_mutation_sql.py` | Create | Mutation row lookup, identity checks, and canonical idempotency comparisons | ≤ 120 |
| `src/code_agent/sessions/_rewind_mutations.py` | Create | Coverage and mutation prepare/complete/abort/gap transactions | ≤ 240 |
| `src/code_agent/sessions/_rewind_checkpoints.py` | Create | Anchor validation and anchored checkpoint write transaction | ≤ 165 |
| `src/code_agent/sessions/_rewind_observations.py` | Create | Bounded list/observe/head read transactions | ≤ 270 |
| `src/code_agent/sessions/rewind_repository.py` | Create | Narrow public subclass composing the three rewind mixins | ≤ 20 |
| `src/code_agent/sessions/_database.py` | Modify | Register schema v11 and its required columns only | ≤ 250 |
| `src/code_agent/sessions/tests/test_rewind_models.py` | Create | Strict-model and canonical-codec tests | ≤ 230 |
| `src/code_agent/sessions/tests/test_rewind_migrations.py` | Create | v10→v11 empty-table and schema-integrity tests | ≤ 145 |
| `src/code_agent/sessions/tests/test_rewind_mutations.py` | Create | Coverage/idempotency/state-machine/rollback tests | ≤ 300 |
| `src/code_agent/sessions/tests/test_rewind_anchors.py` | Create | Anchor atomicity and conversation/code scope separation tests | ≤ 200 |
| `src/code_agent/sessions/tests/test_rewind_observations.py` | Create | Thread-filtered bounds, limits, heads, ordering, and pagination tests | ≤ 235 |
| `src/code_agent/sessions/AGENTS.md` | Modify | Append only implemented Sessions Units | ≤ 45 |

Do not modify `src/code_agent/sessions/repository.py`, `src/code_agent/sessions/_records.py`, Workspace, Core, Interfaces, `code_agent_win`, root tests, or root configuration. Run every command from the repository root. Before the first command, resolve the interpreter once:

```powershell
$python = (Get-Command python).Source
```

## Task 1: Add strict frozen records and canonical codecs

**Files:**
- Create: `src/code_agent/sessions/tests/test_rewind_models.py`
- Create: `src/code_agent/sessions/_rewind_model_base.py`
- Create: `src/code_agent/sessions/_rewind_model_records.py`
- Create: `src/code_agent/sessions/rewind_models.py`
- Create: `src/code_agent/sessions/_rewind_codec.py`

- [ ] **Step 1: Write the failing model and codec tests**

Create `src/code_agent/sessions/tests/test_rewind_models.py` with this complete content:

```python
from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._rewind_codec import (  # noqa: E402
    decode_rewind_cursor,
    decode_rewind_handle,
    encode_rewind_cursor,
    encode_rewind_handle,
)
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.rewind_models import (  # noqa: E402
    CoverageToken,
    RewindBaseline,
    RewindCheckpointFact,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
    RewindReadLimits,
)


class RewindModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 17, tzinfo=timezone.utc)

    def test_enum_values_are_stable(self) -> None:
        self.assertEqual(
            tuple(item.value for item in RewindCoverageState),
            ("active", "invalidated"),
        )
        self.assertEqual(
            tuple(item.value for item in RewindMutationStatus),
            ("prepared", "completed", "aborted", "gap"),
        )
        self.assertEqual(
            tuple(item.value for item in RewindBaseline),
            (
                "git-staged",
                "git-unstaged",
                "git-untracked",
                "non-git-existing",
                "absent",
                "unknown",
            ),
        )

    def test_token_and_limits_reject_invalid_bounds(self) -> None:
        with self.assertRaises(ValueError):
            CoverageToken("A" * 64, 1)
        with self.assertRaises(ValueError):
            CoverageToken("a" * 64, 0)
        with self.assertRaises(ValueError):
            RewindReadLimits(0, 1)
        with self.assertRaises(ValueError):
            RewindReadLimits(257, 1)

    def test_path_fact_requires_canonical_unique_hash_state(self) -> None:
        with self.assertRaises(ValueError):
            RewindMutationPath(
                "../note.txt",
                False,
                None,
                RewindBaseline.ABSENT,
                True,
                "1" * 64,
            )
        with self.assertRaises(ValueError):
            RewindMutationPath(
                "note.txt",
                False,
                "0" * 64,
                RewindBaseline.ABSENT,
                True,
                "1" * 64,
            )
        with self.assertRaises(ValueError):
            RewindMutationPath(
                "note.txt",
                True,
                "0" * 64,
                RewindBaseline.ABSENT,
                True,
                "1" * 64,
            )

    def test_prepare_deep_freezes_handle_and_paths(self) -> None:
        handle = {"identifier": "a" * 32, "paths": ["note.txt"]}
        path = RewindMutationPath(
            "note.txt",
            False,
            None,
            RewindBaseline.ABSENT,
            True,
            "1" * 64,
        )
        request = RewindMutationPrepare(
            CoverageToken("b" * 64, 1),
            "thread-root",
            "thread-child",
            None,
            "delegate-1",
            "request-1",
            "write_file",
            handle,
            (path,),
        )
        handle["paths"].append("changed.txt")
        self.assertEqual(request.snapshot_handle["paths"], ("note.txt",))
        with self.assertRaises(TypeError):
            request.snapshot_handle["identifier"] = "changed"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            request.request_id = "changed"  # type: ignore[misc]

    def test_prepare_rejects_duplicate_paths_and_oversized_handle(self) -> None:
        path = RewindMutationPath(
            "note.txt",
            False,
            None,
            RewindBaseline.ABSENT,
            True,
            "1" * 64,
        )
        arguments = (
            CoverageToken("b" * 64, 1),
            "owner",
            "origin",
            None,
            None,
            "request",
            "write_file",
        )
        with self.assertRaises(ValueError):
            RewindMutationPrepare(*arguments, {"id": "ok"}, (path, path))
        with self.assertRaises(ValueError):
            RewindMutationPrepare(*arguments, {"value": "x" * 70_000}, (path,))

    def test_checkpoint_fact_separates_conversation_from_code_owner(self) -> None:
        fact = RewindCheckpointFact(
            "checkpoint-child",
            "thread-root",
            CoverageToken("a" * 64, 1),
            7,
            RewindCoverageState.ACTIVE,
            self.now,
        )
        self.assertEqual(fact.checkpoint_id, "checkpoint-child")
        self.assertEqual(fact.owner_thread_id, "thread-root")
        self.assertEqual(fact.workspace_fingerprint, "a" * 64)
        self.assertEqual(fact.generation, 1)

    def test_handle_codec_is_canonical_bounded_and_deep_frozen(self) -> None:
        first = {"z": [1, "two"], "a": {"ok": True}}
        second = {"a": {"ok": True}, "z": [1, "two"]}
        encoded = encode_rewind_handle(first)
        self.assertEqual(encoded, encode_rewind_handle(second))
        decoded = decode_rewind_handle(encoded)
        self.assertEqual(decoded["z"], (1, "two"))
        with self.assertRaises(SessionCorruptionError):
            decode_rewind_handle('{"z":1, "a":2}')

    def test_cursor_codec_rejects_noncanonical_or_unbounded_values(self) -> None:
        cursor = encode_rewind_cursor(
            "2026-07-17T00:00:00Z", "checkpoint-1"
        )
        self.assertEqual(
            decode_rewind_cursor(cursor),
            ("2026-07-17T00:00:00Z", "checkpoint-1"),
        )
        for invalid in ("", cursor + "=", "!", "a" * 1_025):
            with self.subTest(invalid=invalid[:10]):
                with self.assertRaises(ValueError):
                    decode_rewind_cursor(invalid)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and observe RED**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_models.py' -v
```

Expected: `ERROR` with `ModuleNotFoundError: No module named 'code_agent.sessions._rewind_codec'`; zero tests pass accidentally.

- [ ] **Step 3: Implement the focused frozen model modules**

Create `src/code_agent/sessions/_rewind_model_base.py` with this complete content:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping

from code_agent.core._json import JSONValue, freeze_mapping, plain

from .models import CheckpointRecord


MAX_REWIND_ACTION_PATHS = 32
MAX_REWIND_HANDLE_BYTES = 64 * 1024
MAX_REWIND_TEXT_FIELD = 512
DEFAULT_REWIND_MAX_MUTATIONS = 256
DEFAULT_REWIND_MAX_PATHS = 512
MAX_REWIND_PAGE_SIZE = 100
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


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


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    if len(value) > MAX_REWIND_TEXT_FIELD:
        raise ValueError(f"{name} exceeds its text limit")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")
    return value


def _sha256(value: object, name: str) -> str:
    if type(value) is not str or _HEX_64.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hex characters")
    return value


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _relative_path(value: object) -> str:
    if type(value) is not str or not value or "\0" in value or "\\" in value:
        raise ValueError("path must be a canonical relative POSIX path")
    parts = value.split("/")
    if Path(value).is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise ValueError("path must be a canonical relative POSIX path")
    if len(value) > MAX_REWIND_TEXT_FIELD:
        raise ValueError("path exceeds its text limit")
    return value


def _existence_hash(existed: object, digest: object, prefix: str) -> tuple[bool, str | None]:
    if type(existed) is not bool:
        raise TypeError(f"{prefix}_existed must be a bool")
    if existed:
        return existed, _sha256(digest, f"{prefix}_sha256")
    if digest is not None:
        raise ValueError(f"{prefix}_sha256 must be None when absent")
    return existed, None


@dataclass(frozen=True)
class CoverageToken:
    workspace_fingerprint: str
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_fingerprint",
            _sha256(self.workspace_fingerprint, "workspace_fingerprint"),
        )
        object.__setattr__(
            self, "generation", _integer(self.generation, "generation", positive=True)
        )


@dataclass(frozen=True)
class RewindMutationPath:
    path: str
    before_existed: bool
    before_sha256: str | None
    baseline: RewindBaseline
    after_existed: bool
    after_sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _relative_path(self.path))
        before = _existence_hash(
            self.before_existed, self.before_sha256, "before"
        )
        after = _existence_hash(self.after_existed, self.after_sha256, "after")
        object.__setattr__(self, "before_existed", before[0])
        object.__setattr__(self, "before_sha256", before[1])
        object.__setattr__(self, "after_existed", after[0])
        object.__setattr__(self, "after_sha256", after[1])
        if not isinstance(self.baseline, RewindBaseline):
            raise TypeError("baseline must be a RewindBaseline")
        if self.baseline is RewindBaseline.ABSENT and self.before_existed:
            raise ValueError("absent baseline requires an absent preimage")


def _paths(value: object, *, allow_empty: bool) -> tuple[RewindMutationPath, ...]:
    if type(value) is not tuple or any(
        not isinstance(item, RewindMutationPath) for item in value
    ):
        raise TypeError("paths must be a tuple of RewindMutationPath")
    if (not allow_empty and not value) or len(value) > MAX_REWIND_ACTION_PATHS:
        raise ValueError("paths are outside the supported action bound")
    names = tuple(item.path for item in value)
    if len(names) != len(set(names)):
        raise ValueError("mutation paths must be unique")
    return value


def _handle(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise TypeError("snapshot_handle must be a mapping")
    frozen = freeze_mapping(value, "snapshot_handle")
    encoded = json.dumps(
        plain(frozen), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_REWIND_HANDLE_BYTES:
        raise ValueError("snapshot_handle exceeds its encoded byte limit")
    return frozen


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
        if not isinstance(self.coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        for name in ("owner_thread_id", "origin_thread_id", "request_id", "action_name"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("task_id", "parent_request_id"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(self, "snapshot_handle", _handle(self.snapshot_handle))
        object.__setattr__(self, "paths", _paths(self.paths, allow_empty=False))


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
        if not isinstance(self.coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        for name in (
            "owner_thread_id",
            "origin_thread_id",
            "request_id",
            "action_name",
            "reason",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("task_id", "parent_request_id"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))


@dataclass(frozen=True)
class RewindCoverageRecord:
    token: CoverageToken
    state: RewindCoverageState
    mutation_high_water: int
    invalidation_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.token, CoverageToken):
            raise TypeError("token must be a CoverageToken")
        if not isinstance(self.state, RewindCoverageState):
            raise TypeError("state must be a RewindCoverageState")
        object.__setattr__(
            self,
            "mutation_high_water",
            _integer(self.mutation_high_water, "mutation_high_water"),
        )
        reason = _optional_text(self.invalidation_reason, "invalidation_reason")
        if (self.state is RewindCoverageState.ACTIVE) != (reason is None):
            raise ValueError("coverage state and invalidation reason disagree")
        object.__setattr__(self, "invalidation_reason", reason)

    @property
    def workspace_fingerprint(self) -> str:
        return self.token.workspace_fingerprint

    @property
    def generation(self) -> int:
        return self.token.generation
```

Create `src/code_agent/sessions/_rewind_model_records.py` with this complete content:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from code_agent.core._json import JSONValue

from ._rewind_model_base import (
    CoverageToken,
    DEFAULT_REWIND_MAX_MUTATIONS,
    DEFAULT_REWIND_MAX_PATHS,
    MAX_REWIND_PAGE_SIZE,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationStatus,
    _handle,
    _integer,
    _optional_text,
    _paths,
    _text,
    _utc,
)
from .models import CheckpointRecord

@dataclass(frozen=True)
class RewindMutationRecord:
    mutation_id: str
    sequence: int
    coverage: CoverageToken
    owner_thread_id: str
    origin_thread_id: str
    task_id: str | None
    parent_request_id: str | None
    request_id: str
    action_name: str
    status: RewindMutationStatus
    gap_reason: str | None
    snapshot_handle: Mapping[str, JSONValue] | None
    paths: tuple[RewindMutationPath, ...]
    created_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mutation_id", _text(self.mutation_id, "mutation_id"))
        object.__setattr__(self, "sequence", _integer(self.sequence, "sequence", positive=True))
        if not isinstance(self.coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        for name in ("owner_thread_id", "origin_thread_id", "request_id", "action_name"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("task_id", "parent_request_id", "gap_reason"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        if not isinstance(self.status, RewindMutationStatus):
            raise TypeError("status must be a RewindMutationStatus")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        completed = (
            None
            if self.completed_at is None
            else _utc(self.completed_at, "completed_at")
        )
        object.__setattr__(self, "completed_at", completed)
        is_gap = self.status is RewindMutationStatus.GAP
        if is_gap:
            if self.gap_reason is None or self.snapshot_handle is not None:
                raise ValueError("gap records require only a gap reason")
            object.__setattr__(self, "paths", _paths(self.paths, allow_empty=True))
            if self.paths:
                raise ValueError("gap records cannot contain paths")
        else:
            if self.gap_reason is not None or self.snapshot_handle is None:
                raise ValueError("typed mutation records require a snapshot handle")
            object.__setattr__(self, "snapshot_handle", _handle(self.snapshot_handle))
            object.__setattr__(self, "paths", _paths(self.paths, allow_empty=False))
        if (self.status is RewindMutationStatus.PREPARED) != (completed is None):
            raise ValueError("mutation status and completion timestamp disagree")


@dataclass(frozen=True)
class RewindCheckpointAnchor:
    coverage: CoverageToken
    owner_thread_id: str
    coverage_state: RewindCoverageState
    mutation_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        object.__setattr__(self, "owner_thread_id", _text(self.owner_thread_id, "owner_thread_id"))
        if not isinstance(self.coverage_state, RewindCoverageState):
            raise TypeError("coverage_state must be a RewindCoverageState")
        object.__setattr__(
            self, "mutation_sequence", _integer(self.mutation_sequence, "mutation_sequence")
        )


@dataclass(frozen=True)
class RewindCheckpointFact:
    checkpoint_id: str
    owner_thread_id: str
    coverage: CoverageToken
    mutation_sequence: int
    coverage_state: RewindCoverageState
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoint_id", _text(self.checkpoint_id, "checkpoint_id"))
        object.__setattr__(self, "owner_thread_id", _text(self.owner_thread_id, "owner_thread_id"))
        if not isinstance(self.coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        object.__setattr__(
            self, "mutation_sequence", _integer(self.mutation_sequence, "mutation_sequence")
        )
        if not isinstance(self.coverage_state, RewindCoverageState):
            raise TypeError("coverage_state must be a RewindCoverageState")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))

    @property
    def workspace_fingerprint(self) -> str:
        return self.coverage.workspace_fingerprint

    @property
    def generation(self) -> int:
        return self.coverage.generation


@dataclass(frozen=True)
class RewindReadLimits:
    max_mutations: int = DEFAULT_REWIND_MAX_MUTATIONS
    max_paths: int = DEFAULT_REWIND_MAX_PATHS

    def __post_init__(self) -> None:
        mutations = _integer(self.max_mutations, "max_mutations", positive=True)
        paths = _integer(self.max_paths, "max_paths", positive=True)
        if mutations > DEFAULT_REWIND_MAX_MUTATIONS:
            raise ValueError("max_mutations exceeds its supported bound")
        if paths > DEFAULT_REWIND_MAX_PATHS:
            raise ValueError("max_paths exceeds its supported bound")
        object.__setattr__(self, "max_mutations", mutations)
        object.__setattr__(self, "max_paths", paths)


@dataclass(frozen=True)
class RewindObservationHeads:
    message_sequence: int
    event_sequence: int
    mutation_sequence: int
    coverage_generation: int | None
    coverage_state: RewindCoverageState | None

    def __post_init__(self) -> None:
        for name in ("message_sequence", "event_sequence", "mutation_sequence"):
            object.__setattr__(self, name, _integer(getattr(self, name), name))
        generation = self.coverage_generation
        if generation is not None:
            generation = _integer(generation, "coverage_generation", positive=True)
        if (generation is None) != (self.coverage_state is None):
            raise ValueError("coverage head fields must be both present or both absent")
        if self.coverage_state is not None and not isinstance(
            self.coverage_state, RewindCoverageState
        ):
            raise TypeError("coverage_state must be a RewindCoverageState or None")
        object.__setattr__(self, "coverage_generation", generation)


@dataclass(frozen=True)
class RewindObservation:
    checkpoint: CheckpointRecord
    checkpoint_fact: RewindCheckpointFact | None
    conversation_message_count: int | None
    heads: RewindObservationHeads
    mutations: tuple[RewindMutationRecord, ...]
    limit_exceeded: bool

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, CheckpointRecord):
            raise TypeError("checkpoint must be a CheckpointRecord")
        if self.checkpoint_fact is not None and not isinstance(
            self.checkpoint_fact, RewindCheckpointFact
        ):
            raise TypeError("checkpoint_fact must be typed or None")
        count = self.conversation_message_count
        if count is not None:
            count = _integer(count, "conversation_message_count")
        object.__setattr__(self, "conversation_message_count", count)
        if not isinstance(self.heads, RewindObservationHeads):
            raise TypeError("heads must be RewindObservationHeads")
        if type(self.mutations) is not tuple or any(
            not isinstance(item, RewindMutationRecord) for item in self.mutations
        ):
            raise TypeError("mutations must be a tuple of records")
        if type(self.limit_exceeded) is not bool:
            raise TypeError("limit_exceeded must be a bool")
        if self.limit_exceeded and self.mutations:
            raise ValueError("limited observations cannot expose a partial chain")


@dataclass(frozen=True)
class RewindCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoint_id", _text(self.checkpoint_id, "checkpoint_id"))
        object.__setattr__(self, "label", _text(self.label, "label"))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if type(self.has_message_bound) is not bool or type(self.has_code_anchor) is not bool:
            raise TypeError("candidate facets must be bool values")


@dataclass(frozen=True)
class RewindCandidatePage:
    items: tuple[RewindCandidate, ...] = field(default_factory=tuple)
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            not isinstance(item, RewindCandidate) for item in self.items
        ):
            raise TypeError("items must be a tuple of candidates")
        object.__setattr__(self, "next_cursor", _optional_text(self.next_cursor, "next_cursor"))
```

Create `src/code_agent/sessions/rewind_models.py` as the stable public facade with this complete content:

```python
from __future__ import annotations

from ._rewind_model_base import (
    DEFAULT_REWIND_MAX_MUTATIONS,
    DEFAULT_REWIND_MAX_PATHS,
    MAX_REWIND_ACTION_PATHS,
    MAX_REWIND_HANDLE_BYTES,
    MAX_REWIND_PAGE_SIZE,
    MAX_REWIND_TEXT_FIELD,
    CoverageToken,
    RewindBaseline,
    RewindCoverageRecord,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
)
from ._rewind_model_records import (
    RewindCandidate,
    RewindCandidatePage,
    RewindCheckpointAnchor,
    RewindCheckpointFact,
    RewindMutationRecord,
    RewindObservation,
    RewindObservationHeads,
    RewindReadLimits,
)


__all__ = (
    "DEFAULT_REWIND_MAX_MUTATIONS", "DEFAULT_REWIND_MAX_PATHS",
    "MAX_REWIND_ACTION_PATHS", "MAX_REWIND_HANDLE_BYTES",
    "MAX_REWIND_PAGE_SIZE", "MAX_REWIND_TEXT_FIELD", "CoverageToken",
    "RewindBaseline", "RewindCandidate", "RewindCandidatePage",
    "RewindCheckpointAnchor", "RewindCheckpointFact", "RewindCoverageRecord",
    "RewindCoverageState", "RewindGapPrepare", "RewindMutationPath",
    "RewindMutationPrepare", "RewindMutationRecord", "RewindMutationStatus",
    "RewindObservation", "RewindObservationHeads", "RewindReadLimits",
)
```

- [ ] **Step 4: Implement the canonical handle and cursor codec**

Create `src/code_agent/sessions/_rewind_codec.py` with this complete content:

```python
from __future__ import annotations

import base64
import binascii
import json
from typing import Mapping, cast

from code_agent.core._json import (
    JSONValue,
    freeze_mapping,
    plain,
    validate_json_mapping,
)

from ._codec import decode_datetime, encode_datetime
from .errors import SessionCorruptionError
from .rewind_models import MAX_REWIND_HANDLE_BYTES, MAX_REWIND_TEXT_FIELD


def encode_rewind_handle(value: Mapping[str, JSONValue]) -> str:
    validate_json_mapping(value, "snapshot_handle")
    encoded = json.dumps(
        plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_REWIND_HANDLE_BYTES:
        raise ValueError("snapshot_handle exceeds its encoded byte limit")
    return encoded


def decode_rewind_handle(value: object) -> Mapping[str, JSONValue]:
    if type(value) is not str:
        raise SessionCorruptionError("invalid rewind snapshot handle")
    try:
        decoded = json.loads(value)
        validate_json_mapping(decoded, "snapshot_handle")
        canonical = encode_rewind_handle(cast(Mapping[str, JSONValue], decoded))
        if canonical != value:
            raise ValueError("snapshot handle is not canonical JSON")
        return freeze_mapping(
            cast(Mapping[str, JSONValue], decoded), "snapshot_handle"
        )
    except SessionCorruptionError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid rewind snapshot handle") from error


def encode_rewind_cursor(created_at: str, checkpoint_id: str) -> str:
    if type(created_at) is not str or type(checkpoint_id) is not str:
        raise TypeError("rewind cursor fields must be strings")
    if (
        not checkpoint_id.strip()
        or len(checkpoint_id) > MAX_REWIND_TEXT_FIELD
        or encode_datetime(decode_datetime(created_at, "rewind cursor")) != created_at
    ):
        raise ValueError("invalid rewind cursor fields")
    payload = json.dumps(
        {"created_at": created_at, "id": checkpoint_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_rewind_cursor(value: str) -> tuple[str, str]:
    try:
        if type(value) is not str or not value or "=" in value:
            raise ValueError
        if len(value) > 2 * MAX_REWIND_TEXT_FIELD:
            raise ValueError
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        decoded = json.loads(raw.decode("utf-8"))
        if type(decoded) is not dict or set(decoded) != {"created_at", "id"}:
            raise ValueError
        created_at = decoded["created_at"]
        checkpoint_id = decoded["id"]
        if (
            type(created_at) is not str
            or type(checkpoint_id) is not str
            or encode_rewind_cursor(created_at, checkpoint_id) != value
        ):
            raise ValueError
        return created_at, checkpoint_id
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError("invalid rewind cursor") from error
```

- [ ] **Step 5: Run GREEN checks**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_models.py' -v
& $python -m compileall -q src/code_agent/sessions
```

Expected: 8 tests pass; compileall exits 0.

- [ ] **Step 6: Commit the model/codec slice**

Run:

```powershell
git diff --check
git add -- src/code_agent/sessions/_rewind_model_base.py src/code_agent/sessions/_rewind_model_records.py src/code_agent/sessions/rewind_models.py src/code_agent/sessions/_rewind_codec.py src/code_agent/sessions/tests/test_rewind_models.py
git diff --cached --name-only
git commit -m "新增回溯模型：冻结可信记录与游标"
```

Expected staged allowlist: exactly the five paths above; commit succeeds.

## Task 2: Add the empty schema-v11 migration

**Files:**
- Create: `src/code_agent/sessions/tests/test_rewind_migrations.py`
- Create: `src/code_agent/sessions/_rewind_schema.py`
- Modify: `src/code_agent/sessions/_database.py`

- [ ] **Step 1: Write the failing migration tests**

Create `src/code_agent/sessions/tests/test_rewind_migrations.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._database import SCHEMA_VERSION, _MIGRATIONS  # noqa: E402
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


def create_v10_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX messages_thread_sequence
                ON messages(thread_id, sequence);
            CREATE INDEX events_thread_sequence
                ON events(thread_id, sequence);
            PRAGMA user_version = 1;
            """
        )
        for version in range(2, 11):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")


class RewindMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v10_migrates_to_v11_with_only_empty_rewind_tables(self) -> None:
        create_v10_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy",
                    "2026-07-17T00:00:00Z",
                    "2026-07-17T00:00:00Z",
                    None,
                    "active",
                ),
            )
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "checkpoint",
                    "legacy",
                    "old",
                    "{}",
                    "2026-07-17T00:00:00Z",
                    0,
                    0,
                ),
            )

        SQLiteSessionRepository(self.database)

        with sqlite3.connect(self.database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "workspace_rewind_coverage",
                    "workspace_mutations",
                    "workspace_mutation_paths",
                    "checkpoint_rewind_facts",
                )
            )
            legacy = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE id = 'checkpoint'"
            ).fetchone()[0]
        self.assertEqual(version, 11)
        self.assertEqual(counts, (0, 0, 0, 0))
        self.assertEqual(legacy, 1)

    def test_v11_missing_rewind_table_fails_schema_check(self) -> None:
        create_v10_database(self.database)
        with sqlite3.connect(self.database) as connection:
            for statement in _MIGRATIONS[11]:
                connection.execute(statement)
            connection.execute("DROP TABLE checkpoint_rewind_facts")
            connection.execute("PRAGMA user_version = 11")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    def test_v11_missing_parent_request_column_fails_schema_check(self) -> None:
        create_v10_database(self.database)
        statements = tuple(
            statement.replace("parent_request_id TEXT,", "")
            for statement in _MIGRATIONS[11]
        )
        with sqlite3.connect(self.database) as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 11")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run migration tests and observe RED**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_migrations.py' -v
```

Expected: the first setup observes schema version 10 but the test expects a missing migration entry/table; at least one test errors or fails because `_MIGRATIONS[11]` does not exist.

- [ ] **Step 3: Define the complete v11 schema**

Create `src/code_agent/sessions/_rewind_schema.py` with this complete content:

```python
from __future__ import annotations


REWIND_MIGRATION = (
    "CREATE TABLE workspace_rewind_coverage ("
    "workspace_fingerprint TEXT PRIMARY KEY, "
    "generation INTEGER NOT NULL CHECK(generation > 0), "
    "state TEXT NOT NULL CHECK(state IN ('active','invalidated')), "
    "mutation_high_water INTEGER NOT NULL DEFAULT 0 "
    "CHECK(mutation_high_water >= 0), "
    "invalidation_reason TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "CHECK ((state = 'active' AND invalidation_reason IS NULL) OR "
    "(state = 'invalidated' AND invalidation_reason IS NOT NULL)))",
    "CREATE TABLE workspace_mutations ("
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
    "mutation_id TEXT NOT NULL UNIQUE, "
    "workspace_fingerprint TEXT NOT NULL "
    "REFERENCES workspace_rewind_coverage(workspace_fingerprint), "
    "coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0), "
    "owner_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "origin_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "task_id TEXT REFERENCES tasks(id), parent_request_id TEXT, "
    "request_id TEXT NOT NULL, action_name TEXT NOT NULL, "
    "status TEXT NOT NULL "
    "CHECK(status IN ('prepared','completed','aborted','gap')), "
    "gap_reason TEXT, snapshot_handle TEXT, created_at TEXT NOT NULL, "
    "completed_at TEXT, "
    "UNIQUE(workspace_fingerprint, origin_thread_id, request_id), "
    "CHECK ((status = 'gap' AND gap_reason IS NOT NULL "
    "AND snapshot_handle IS NULL AND completed_at IS NOT NULL) OR "
    "(status = 'prepared' AND gap_reason IS NULL "
    "AND snapshot_handle IS NOT NULL AND completed_at IS NULL) OR "
    "(status IN ('completed','aborted') AND gap_reason IS NULL "
    "AND snapshot_handle IS NOT NULL AND completed_at IS NOT NULL)))",
    "CREATE TABLE workspace_mutation_paths ("
    "mutation_sequence INTEGER NOT NULL "
    "REFERENCES workspace_mutations(sequence) ON DELETE CASCADE, "
    "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), path TEXT NOT NULL, "
    "pre_existed INTEGER NOT NULL CHECK(pre_existed IN (0,1)), "
    "pre_sha256 TEXT, baseline TEXT NOT NULL CHECK(baseline IN "
    "('git-staged','git-unstaged','git-untracked',"
    "'non-git-existing','absent','unknown')), "
    "post_existed INTEGER NOT NULL CHECK(post_existed IN (0,1)), "
    "post_sha256 TEXT, PRIMARY KEY(mutation_sequence, ordinal), "
    "UNIQUE(mutation_sequence, path), "
    "CHECK ((pre_existed = 1 AND pre_sha256 IS NOT NULL) OR "
    "(pre_existed = 0 AND pre_sha256 IS NULL)), "
    "CHECK ((post_existed = 1 AND post_sha256 IS NOT NULL) OR "
    "(post_existed = 0 AND post_sha256 IS NULL)))",
    "CREATE TABLE checkpoint_rewind_facts ("
    "checkpoint_id TEXT PRIMARY KEY "
    "REFERENCES checkpoints(id) ON DELETE CASCADE, "
    "owner_thread_id TEXT NOT NULL REFERENCES threads(id), "
    "workspace_fingerprint TEXT NOT NULL "
    "REFERENCES workspace_rewind_coverage(workspace_fingerprint), "
    "coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0), "
    "mutation_sequence INTEGER NOT NULL CHECK(mutation_sequence >= 0), "
    "coverage_state TEXT NOT NULL "
    "CHECK(coverage_state IN ('active','invalidated')), "
    "created_at TEXT NOT NULL)",
    "CREATE INDEX workspace_mutations_workspace_sequence "
    "ON workspace_mutations(workspace_fingerprint, sequence)",
    "CREATE INDEX workspace_mutations_owner_sequence "
    "ON workspace_mutations(owner_thread_id, sequence)",
    "CREATE INDEX workspace_mutation_paths_path_sequence "
    "ON workspace_mutation_paths(path, mutation_sequence)",
    "CREATE INDEX checkpoint_rewind_facts_owner "
    "ON checkpoint_rewind_facts(owner_thread_id, mutation_sequence)",
)

REWIND_REQUIRED_COLUMNS = {
    "workspace_rewind_coverage": {
        "workspace_fingerprint",
        "generation",
        "state",
        "mutation_high_water",
        "invalidation_reason",
        "started_at",
        "updated_at",
    },
    "workspace_mutations": {
        "sequence",
        "mutation_id",
        "workspace_fingerprint",
        "coverage_generation",
        "owner_thread_id",
        "origin_thread_id",
        "task_id",
        "parent_request_id",
        "request_id",
        "action_name",
        "status",
        "gap_reason",
        "snapshot_handle",
        "created_at",
        "completed_at",
    },
    "workspace_mutation_paths": {
        "mutation_sequence",
        "ordinal",
        "path",
        "pre_existed",
        "pre_sha256",
        "baseline",
        "post_existed",
        "post_sha256",
    },
    "checkpoint_rewind_facts": {
        "checkpoint_id",
        "owner_thread_id",
        "workspace_fingerprint",
        "coverage_generation",
        "mutation_sequence",
        "coverage_state",
        "created_at",
    },
}
```

- [ ] **Step 4: Register v11 in the existing database owner**

Apply this exact patch to `src/code_agent/sessions/_database.py`:

```diff
@@
 from .errors import (
@@
 )
+from ._rewind_schema import REWIND_MIGRATION, REWIND_REQUIRED_COLUMNS
-SCHEMA_VERSION = 10
+SCHEMA_VERSION = 11
@@
     10: (
         "ALTER TABLE checkpoints ADD COLUMN message_sequence INTEGER",
         "ALTER TABLE checkpoints ADD COLUMN event_sequence INTEGER",
     ),
+    11: REWIND_MIGRATION,
 }
@@
     "task_completions": {"task_id", "revision", "generation", "subject_hash", "assessment", "created_at"},
+    **REWIND_REQUIRED_COLUMNS,
 }
```

- [ ] **Step 5: Run GREEN checks**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_migrations.py' -v
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_migrations.py' -v
& $python -m compileall -q src/code_agent/sessions
```

Expected: all new migration tests and all existing migration tests pass; compileall exits 0. The v10 fixture retains its checkpoint but all four rewind tables contain zero rows.

- [ ] **Step 6: Commit the schema slice**

Run:

```powershell
git diff --check
git add -- src/code_agent/sessions/_rewind_schema.py src/code_agent/sessions/_database.py src/code_agent/sessions/tests/test_rewind_migrations.py
git diff --cached --name-only
git commit -m "迁移会话存储：建立空白回溯日志表"
```

Expected staged allowlist: exactly the three paths above; commit succeeds.

## Task 3: Implement the atomic coverage and mutation state machine

**Files:**
- Create: `src/code_agent/sessions/tests/test_rewind_mutations.py`
- Create: `src/code_agent/sessions/_rewind_rows.py`
- Create: `src/code_agent/sessions/_rewind_mutation_sql.py`
- Create: `src/code_agent/sessions/_rewind_mutations.py`
- Create: `src/code_agent/sessions/rewind_repository.py`

- [ ] **Step 1: Write the failing mutation lifecycle tests**

Create `src/code_agent/sessions/tests/test_rewind_mutations.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.rewind_models import (  # noqa: E402
    CoverageToken,
    RewindBaseline,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def path_fact(name: str = "note.txt") -> RewindMutationPath:
    return RewindMutationPath(
        name,
        False,
        None,
        RewindBaseline.ABSENT,
        True,
        "1" * 64,
    )


def prepare(
    coverage: CoverageToken,
    owner: str,
    request_id: str,
    *,
    origin: str | None = None,
    task_id: str | None = None,
    parent_request_id: str | None = None,
    action_name: str = "write_file",
    handle: dict[str, object] | None = None,
    paths: tuple[RewindMutationPath, ...] | None = None,
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        coverage,
        owner,
        owner if origin is None else origin,
        task_id,
        parent_request_id,
        request_id,
        action_name,
        {"identifier": "snapshot"} if handle is None else handle,
        (path_fact(),) if paths is None else paths,
    )


def gap(
    coverage: CoverageToken,
    owner: str,
    request_id: str,
    *,
    reason: str = "unknown-workspace-writer",
) -> RewindGapPrepare:
    return RewindGapPrepare(
        coverage,
        owner,
        owner,
        None,
        None,
        request_id,
        "run_command",
        reason,
    )


class RewindMutationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_ensure_coverage_is_idempotent(self) -> None:
        first = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        second = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        self.assertEqual(first, second)
        self.assertEqual(first.generation, 1)
        self.assertEqual(first.state, RewindCoverageState.ACTIVE)
        self.assertEqual(first.mutation_high_water, 0)

    async def test_prepare_retains_owner_origin_task_and_parent(self) -> None:
        owner = await self.repository.create_thread()
        origin = await self.repository.create_thread()
        task = await self.repository.create_task(
            owner,
            TaskContract("edit", TaskAuthorization.local_workspace(".")),
        )
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        record = await self.repository.prepare_rewind_mutation(
            prepare(
                coverage.token,
                owner,
                "child-write",
                origin=origin,
                task_id=task.id,
                parent_request_id="delegate-1",
            )
        )
        self.assertEqual(record.owner_thread_id, owner)
        self.assertEqual(record.origin_thread_id, origin)
        self.assertEqual(record.task_id, task.id)
        self.assertEqual(record.parent_request_id, "delegate-1")
        self.assertEqual(record.status, RewindMutationStatus.PREPARED)
        self.assertEqual(len(record.mutation_id), 32)

    async def test_identical_prepare_is_idempotent(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        request = prepare(coverage.token, owner, "same")
        first = await self.repository.prepare_rewind_mutation(request)
        second = await self.repository.prepare_rewind_mutation(request)
        self.assertEqual(first, second)
        self.assertEqual(first.sequence, 1)

    async def test_reused_key_with_different_payload_is_rejected(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "collision")
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(
                prepare(
                    coverage.token,
                    owner,
                    "collision",
                    action_name="replace_text",
                )
            )
        with sqlite3.connect(self.database) as connection:
            counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutations"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutation_paths"
                ).fetchone()[0],
            )
        self.assertEqual(counts, (1, 1))

    async def test_failed_path_insert_rolls_back_prepare_and_high_water(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TRIGGER reject_second_path BEFORE INSERT "
                "ON workspace_mutation_paths WHEN NEW.ordinal = 1 "
                "BEGIN SELECT RAISE(ABORT, 'reject second path'); END"
            )
        request = prepare(
            coverage.token,
            owner,
            "two-paths",
            paths=(path_fact("a.txt"), path_fact("b.txt")),
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(request)
        after = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with sqlite3.connect(self.database) as connection:
            counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutations"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutation_paths"
                ).fetchone()[0],
            )
        self.assertEqual(after.mutation_high_water, 0)
        self.assertEqual(counts, (0, 0))

    async def test_complete_and_abort_accept_only_prepared_records(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        completed = await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "complete")
        )
        aborted = await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "abort")
        )
        completed = await self.repository.complete_rewind_mutation(
            completed.mutation_id
        )
        aborted = await self.repository.abort_rewind_mutation(
            aborted.mutation_id
        )
        self.assertEqual(completed.status, RewindMutationStatus.COMPLETED)
        self.assertEqual(aborted.status, RewindMutationStatus.ABORTED)
        with self.assertRaises(SessionStorageError):
            await self.repository.abort_rewind_mutation(completed.mutation_id)
        with self.assertRaises(SessionStorageError):
            await self.repository.complete_rewind_mutation(aborted.mutation_id)
        with self.assertRaises(SessionNotFound):
            await self.repository.complete_rewind_mutation("f" * 32)

    async def test_gap_invalidates_and_repeat_gaps_remain_durable(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        first = await self.repository.record_rewind_gap(
            gap(active.token, owner, "gap-1", reason="first-gap")
        )
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        second = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-2", reason="later-gap")
        )
        again = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-2", reason="later-gap")
        )
        after = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        self.assertEqual(first.status, RewindMutationStatus.GAP)
        self.assertEqual(second, again)
        self.assertGreater(second.sequence, first.sequence)
        self.assertEqual(after.state, RewindCoverageState.INVALIDATED)
        self.assertEqual(after.generation, active.generation)
        self.assertEqual(after.invalidation_reason, "first-gap")

    async def test_invalidated_coverage_rejects_prepare_but_accepts_gap(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.record_rewind_gap(gap(active.token, owner, "gap"))
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(
                prepare(invalidated.token, owner, "write")
            )
        durable = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-after")
        )
        self.assertEqual(durable.status, RewindMutationStatus.GAP)

    async def test_handle_keys_cannot_forge_journal_columns(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        record = await self.repository.prepare_rewind_mutation(
            prepare(
                coverage.token,
                owner,
                "opaque",
                handle={"status": "gap", "sequence": 999, "generation": 999},
            )
        )
        self.assertEqual(record.status, RewindMutationStatus.PREPARED)
        self.assertEqual(record.sequence, 1)
        self.assertEqual(record.coverage.generation, 1)
        self.assertEqual(record.snapshot_handle["status"], "gap")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the lifecycle tests and observe RED**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_mutations.py' -v
```

Expected: `ERROR` because `code_agent.sessions.rewind_repository` does not exist.

- [ ] **Step 3: Add the strict shared row decoder**

Create `src/code_agent/sessions/_rewind_rows.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ._codec import decode_datetime, decode_metadata
from ._rewind_codec import decode_rewind_handle
from .errors import SessionCorruptionError
from .models import CheckpointRecord
from .rewind_models import (
    CoverageToken,
    RewindBaseline,
    RewindCheckpointFact,
    RewindCoverageRecord,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationRecord,
    RewindMutationStatus,
)


def coverage_record(row: sqlite3.Row) -> RewindCoverageRecord:
    try:
        return RewindCoverageRecord(
            CoverageToken(row["workspace_fingerprint"], row["generation"]),
            RewindCoverageState(row["state"]),
            row["mutation_high_water"],
            row["invalidation_reason"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind coverage record") from error


def mutation_path(row: sqlite3.Row) -> RewindMutationPath:
    try:
        pre = row["pre_existed"]
        post = row["post_existed"]
        if pre not in (0, 1) or post not in (0, 1):
            raise ValueError("invalid persisted existence flag")
        return RewindMutationPath(
            row["path"],
            bool(pre),
            row["pre_sha256"],
            RewindBaseline(row["baseline"]),
            bool(post),
            row["post_sha256"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind mutation path") from error


def mutation_record(
    row: sqlite3.Row,
    path_rows: Sequence[sqlite3.Row],
) -> RewindMutationRecord:
    try:
        handle = row["snapshot_handle"]
        return RewindMutationRecord(
            row["mutation_id"],
            row["sequence"],
            CoverageToken(
                row["workspace_fingerprint"], row["coverage_generation"]
            ),
            row["owner_thread_id"],
            row["origin_thread_id"],
            row["task_id"],
            row["parent_request_id"],
            row["request_id"],
            row["action_name"],
            RewindMutationStatus(row["status"]),
            row["gap_reason"],
            None if handle is None else decode_rewind_handle(handle),
            tuple(mutation_path(item) for item in path_rows),
            decode_datetime(row["created_at"], "rewind mutation"),
            (
                None
                if row["completed_at"] is None
                else decode_datetime(row["completed_at"], "rewind mutation")
            ),
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind mutation record") from error


def checkpoint_record(row: sqlite3.Row) -> CheckpointRecord:
    try:
        return CheckpointRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            label=row["label"],
            metadata=decode_metadata(row["metadata"]),
            created_at=decode_datetime(row["created_at"], "checkpoint"),
            message_sequence=row["message_sequence"],
            event_sequence=row["event_sequence"],
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted checkpoint") from error


def checkpoint_fact(row: sqlite3.Row) -> RewindCheckpointFact | None:
    if row["rewind_owner_thread_id"] is None:
        return None
    try:
        return RewindCheckpointFact(
            row["id"],
            row["rewind_owner_thread_id"],
            CoverageToken(
                row["workspace_fingerprint"], row["coverage_generation"]
            ),
            row["rewind_mutation_sequence"],
            RewindCoverageState(row["rewind_coverage_state"]),
            decode_datetime(row["rewind_created_at"], "checkpoint rewind fact"),
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid checkpoint rewind fact") from error
```

- [ ] **Step 4: Implement the focused mutation transaction modules**

Create `src/code_agent/sessions/_rewind_mutation_sql.py` with this complete content:

```python
from __future__ import annotations

import sqlite3

from ._records import _require_thread
from ._rewind_rows import coverage_record, mutation_record
from .errors import SessionNotFound, SessionStorageError
from .rewind_models import (
    CoverageToken,
    RewindCoverageRecord,
    RewindGapPrepare,
    RewindMutationPrepare,
    RewindMutationRecord,
    RewindMutationStatus,
)

def _load_coverage(
    connection: sqlite3.Connection, token: CoverageToken
) -> RewindCoverageRecord:
    row = connection.execute(
        "SELECT * FROM workspace_rewind_coverage "
        "WHERE workspace_fingerprint = ?",
        (token.workspace_fingerprint,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("rewind coverage not found")
    record = coverage_record(row)
    if record.token != token:
        raise SessionStorageError("rewind coverage generation moved")
    return record


def _require_identity_rows(
    connection: sqlite3.Connection,
    request: RewindMutationPrepare | RewindGapPrepare,
) -> None:
    _require_thread(connection, request.owner_thread_id)
    _require_thread(connection, request.origin_thread_id)
    if request.task_id is not None:
        row = connection.execute(
            "SELECT 1 FROM tasks WHERE id = ?", (request.task_id,)
        ).fetchone()
        if row is None:
            raise SessionNotFound("task not found")


def _load_idempotent(
    connection: sqlite3.Connection,
    fingerprint: str,
    origin_thread_id: str,
    request_id: str,
) -> RewindMutationRecord | None:
    row = connection.execute(
        "SELECT mutation_id FROM workspace_mutations "
        "WHERE workspace_fingerprint = ? AND origin_thread_id = ? "
        "AND request_id = ?",
        (fingerprint, origin_thread_id, request_id),
    ).fetchone()
    return None if row is None else _load_by_id(connection, row["mutation_id"])


def _load_by_id(
    connection: sqlite3.Connection, mutation_id: str
) -> RewindMutationRecord:
    row = connection.execute(
        "SELECT * FROM workspace_mutations WHERE mutation_id = ?",
        (mutation_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("rewind mutation not found")
    paths = connection.execute(
        "SELECT * FROM workspace_mutation_paths "
        "WHERE mutation_sequence = ? ORDER BY ordinal",
        (row["sequence"],),
    ).fetchall()
    return mutation_record(row, paths)


def _shared_identity(
    record: RewindMutationRecord,
    request: RewindMutationPrepare | RewindGapPrepare,
) -> bool:
    return (
        record.coverage == request.coverage
        and record.owner_thread_id == request.owner_thread_id
        and record.origin_thread_id == request.origin_thread_id
        and record.task_id == request.task_id
        and record.parent_request_id == request.parent_request_id
        and record.request_id == request.request_id
        and record.action_name == request.action_name
    )


def _matches_prepare(
    record: RewindMutationRecord, request: RewindMutationPrepare
) -> bool:
    return (
        record.status is not RewindMutationStatus.GAP
        and _shared_identity(record, request)
        and record.snapshot_handle == request.snapshot_handle
        and record.paths == request.paths
    )


def _matches_gap(record: RewindMutationRecord, request: RewindGapPrepare) -> bool:
    return (
        record.status is RewindMutationStatus.GAP
        and _shared_identity(record, request)
        and record.gap_reason == request.reason
    )
```

Create `src/code_agent/sessions/_rewind_mutations.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import uuid

from ._codec import encode_datetime, utc_now
from ._records import _require_thread, _text
from ._rewind_codec import encode_rewind_handle
from ._rewind_rows import coverage_record, mutation_record
from .errors import SessionNotFound, SessionStorageError
from ._rewind_mutation_sql import (
    _load_by_id, _load_coverage, _load_idempotent, _matches_gap,
    _matches_prepare, _require_identity_rows,
)
from .rewind_models import (
    CoverageToken,
    RewindCoverageRecord,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPrepare,
    RewindMutationRecord,
    RewindMutationStatus,
)


class RewindMutationRepositoryMixin:
    _database: object

    async def ensure_rewind_coverage(
        self, workspace_fingerprint: str
    ) -> RewindCoverageRecord:
        fingerprint = CoverageToken(workspace_fingerprint, 1).workspace_fingerprint
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindCoverageRecord:
            connection.execute(
                "INSERT OR IGNORE INTO workspace_rewind_coverage("
                "workspace_fingerprint, generation, state, mutation_high_water, "
                "invalidation_reason, started_at, updated_at"
                ") VALUES (?, 1, 'active', 0, NULL, ?, ?)",
                (fingerprint, timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is None:
                raise SessionStorageError("rewind coverage was not persisted")
            return coverage_record(row)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def prepare_rewind_mutation(
        self, request: RewindMutationPrepare
    ) -> RewindMutationRecord:
        if not isinstance(request, RewindMutationPrepare):
            raise TypeError("request must be a RewindMutationPrepare")
        encoded_handle = encode_rewind_handle(request.snapshot_handle)
        timestamp = encode_datetime(utc_now())
        mutation_id = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            coverage = _load_coverage(connection, request.coverage)
            if coverage.state is not RewindCoverageState.ACTIVE:
                raise SessionStorageError("rewind coverage is not active")
            existing = _load_idempotent(
                connection,
                request.coverage.workspace_fingerprint,
                request.origin_thread_id,
                request.request_id,
            )
            if existing is not None:
                if not _matches_prepare(existing, request):
                    raise SessionStorageError("rewind idempotency key was reused")
                return existing
            _require_identity_rows(connection, request)
            cursor = connection.execute(
                "INSERT INTO workspace_mutations("
                "mutation_id, workspace_fingerprint, coverage_generation, "
                "owner_thread_id, origin_thread_id, task_id, parent_request_id, "
                "request_id, action_name, status, gap_reason, snapshot_handle, "
                "created_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, ?, ?, NULL)",
                (
                    mutation_id,
                    request.coverage.workspace_fingerprint,
                    request.coverage.generation,
                    request.owner_thread_id,
                    request.origin_thread_id,
                    request.task_id,
                    request.parent_request_id,
                    request.request_id,
                    request.action_name,
                    encoded_handle,
                    timestamp,
                ),
            )
            sequence = int(cursor.lastrowid)
            for ordinal, path in enumerate(request.paths):
                connection.execute(
                    "INSERT INTO workspace_mutation_paths VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sequence,
                        ordinal,
                        path.path,
                        int(path.before_existed),
                        path.before_sha256,
                        path.baseline.value,
                        int(path.after_existed),
                        path.after_sha256,
                    ),
                )
            changed = connection.execute(
                "UPDATE workspace_rewind_coverage "
                "SET mutation_high_water = ?, updated_at = ? "
                "WHERE workspace_fingerprint = ? AND generation = ? "
                "AND state = 'active' AND mutation_high_water < ?",
                (
                    sequence,
                    timestamp,
                    request.coverage.workspace_fingerprint,
                    request.coverage.generation,
                    sequence,
                ),
            )
            if changed.rowcount != 1:
                raise SessionStorageError("rewind coverage moved during prepare")
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def complete_rewind_mutation(
        self, mutation_id: str
    ) -> RewindMutationRecord:
        return await self._finish_mutation(mutation_id, RewindMutationStatus.COMPLETED)

    async def abort_rewind_mutation(
        self, mutation_id: str
    ) -> RewindMutationRecord:
        return await self._finish_mutation(mutation_id, RewindMutationStatus.ABORTED)

    async def _finish_mutation(
        self,
        mutation_id: str,
        status: RewindMutationStatus,
    ) -> RewindMutationRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            changed = connection.execute(
                "UPDATE workspace_mutations SET status = ?, completed_at = ? "
                "WHERE mutation_id = ? AND status = 'prepared'",
                (status.value, timestamp, mutation_id),
            )
            if changed.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM workspace_mutations WHERE mutation_id = ?",
                    (mutation_id,),
                ).fetchone()
                if exists is None:
                    raise SessionNotFound("rewind mutation not found")
                raise SessionStorageError("rewind mutation is not prepared")
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def record_rewind_gap(
        self, request: RewindGapPrepare
    ) -> RewindMutationRecord:
        if not isinstance(request, RewindGapPrepare):
            raise TypeError("request must be a RewindGapPrepare")
        timestamp = encode_datetime(utc_now())
        mutation_id = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            _load_coverage(connection, request.coverage)
            existing = _load_idempotent(
                connection,
                request.coverage.workspace_fingerprint,
                request.origin_thread_id,
                request.request_id,
            )
            if existing is not None:
                if not _matches_gap(existing, request):
                    raise SessionStorageError("rewind idempotency key was reused")
                return existing
            _require_identity_rows(connection, request)
            cursor = connection.execute(
                "INSERT INTO workspace_mutations("
                "mutation_id, workspace_fingerprint, coverage_generation, "
                "owner_thread_id, origin_thread_id, task_id, parent_request_id, "
                "request_id, action_name, status, gap_reason, snapshot_handle, "
                "created_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'gap', ?, NULL, ?, ?)",
                (
                    mutation_id,
                    request.coverage.workspace_fingerprint,
                    request.coverage.generation,
                    request.owner_thread_id,
                    request.origin_thread_id,
                    request.task_id,
                    request.parent_request_id,
                    request.request_id,
                    request.action_name,
                    request.reason,
                    timestamp,
                    timestamp,
                ),
            )
            sequence = int(cursor.lastrowid)
            changed = connection.execute(
                "UPDATE workspace_rewind_coverage "
                "SET state = 'invalidated', "
                "invalidation_reason = COALESCE(invalidation_reason, ?), "
                "mutation_high_water = ?, updated_at = ? "
                "WHERE workspace_fingerprint = ? AND generation = ?",
                (
                    request.reason,
                    sequence,
                    timestamp,
                    request.coverage.workspace_fingerprint,
                    request.coverage.generation,
                ),
            )
            if changed.rowcount != 1:
                raise SessionStorageError("rewind coverage generation moved")
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]
```

- [ ] **Step 5: Add the narrow public repository**

Create `src/code_agent/sessions/rewind_repository.py` with this complete content:

```python
from __future__ import annotations

from ._rewind_mutations import RewindMutationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
```

- [ ] **Step 6: Run GREEN checks**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_mutations.py' -v
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_models.py' -v
& $python -m compileall -q src/code_agent/sessions
```

Expected: all mutation and model tests pass; compileall exits 0. The invalidated row remains generation 1, its first reason is stable, prepare is rejected, and a distinct repeat gap is persisted.

- [ ] **Step 7: Commit the mutation slice**

Run:

```powershell
git diff --check
git add -- src/code_agent/sessions/_rewind_rows.py src/code_agent/sessions/_rewind_mutation_sql.py src/code_agent/sessions/_rewind_mutations.py src/code_agent/sessions/rewind_repository.py src/code_agent/sessions/tests/test_rewind_mutations.py
git diff --cached --name-only
git commit -m "持久化回溯变更：实现原子日志状态机"
```

Expected staged allowlist: exactly the five paths above; commit succeeds.

## Task 4: Anchor checkpoints and expose bounded atomic observations

**Files:**
- Create: `src/code_agent/sessions/tests/test_rewind_anchors.py`
- Create: `src/code_agent/sessions/tests/test_rewind_observations.py`
- Create: `src/code_agent/sessions/_rewind_checkpoints.py`
- Create: `src/code_agent/sessions/_rewind_observations.py`
- Modify: `src/code_agent/sessions/rewind_repository.py`

- [ ] **Step 1: Write the failing anchor and observation tests**

Create `src/code_agent/sessions/tests/test_rewind_anchors.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.rewind_models import (  # noqa: E402
    RewindBaseline,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindReadLimits,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def mutation_request(
    token: object,
    owner: str,
    origin: str,
    request_id: str,
    paths: tuple[RewindMutationPath, ...],
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        token,  # type: ignore[arg-type]
        owner,
        origin,
        None,
        None,
        request_id,
        "write_file",
        {"identifier": request_id},
        paths,
    )


def path_fact(name: str) -> RewindMutationPath:
    return RewindMutationPath(
        name,
        False,
        None,
        RewindBaseline.ABSENT,
        True,
        "1" * 64,
    )


class RewindObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def completed(
        self,
        token: object,
        owner: str,
        origin: str,
        request_id: str,
        paths: tuple[RewindMutationPath, ...],
    ) -> None:
        record = await self.repository.prepare_rewind_mutation(
            mutation_request(token, owner, origin, request_id, paths)
        )
        await self.repository.complete_rewind_mutation(record.mutation_id)

    async def test_child_checkpoint_keeps_conversation_and_code_scopes_separate(
        self,
    ) -> None:
        root = await self.repository.create_thread()
        child = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, root
        )
        checkpoint = await self.repository.create_checkpoint(
            child, "child checkpoint", rewind_anchor=anchor
        )
        observed = await self.repository.observe_rewind(
            child, checkpoint, RewindReadLimits()
        )
        self.assertEqual(observed.checkpoint.thread_id, child)
        self.assertIsNotNone(observed.checkpoint_fact)
        assert observed.checkpoint_fact is not None
        self.assertEqual(observed.checkpoint_fact.owner_thread_id, root)

    async def test_stale_anchor_and_prepared_anchor_are_rejected(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        stale = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        await self.completed(
            coverage.token, owner, owner, "first", (path_fact("a.txt"),)
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.create_checkpoint(
                owner, "stale", rewind_anchor=stale
            )
        await self.repository.prepare_rewind_mutation(
            mutation_request(
                coverage.token,
                owner,
                owner,
                "pending",
                (path_fact("b.txt"),),
            )
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.get_rewind_checkpoint_anchor(
                coverage.token, owner
            )

    async def test_invalidated_anchor_persists_invalidated_fact(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.record_rewind_gap(
            RewindGapPrepare(
                active.token,
                owner,
                owner,
                None,
                None,
                "gap",
                "run_command",
                "unknown-writer",
            )
        )
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            invalidated.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "after gap", rewind_anchor=anchor
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        assert observed.checkpoint_fact is not None
        self.assertEqual(
            observed.checkpoint_fact.coverage_state,
            RewindCoverageState.INVALIDATED,
        )

    async def test_unanchored_checkpoint_and_metadata_have_no_code_fact(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        checkpoint = await self.repository.create_checkpoint(
            owner,
            "plain",
            {
                "workspace_fingerprint": FINGERPRINT,
                "owner_thread_id": owner,
                "mutation_sequence": 999,
            },
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        self.assertIsNone(observed.checkpoint_fact)

if __name__ == "__main__":
    unittest.main()
```

Create `src/code_agent/sessions/tests/test_rewind_observations.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.rewind_models import (  # noqa: E402
    RewindBaseline,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindReadLimits,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def mutation_request(
    token: object,
    owner: str,
    origin: str,
    request_id: str,
    paths: tuple[RewindMutationPath, ...],
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        token,  # type: ignore[arg-type]
        owner,
        origin,
        None,
        None,
        request_id,
        "write_file",
        {"identifier": request_id},
        paths,
    )


def path_fact(name: str) -> RewindMutationPath:
    return RewindMutationPath(
        name,
        False,
        None,
        RewindBaseline.ABSENT,
        True,
        "1" * 64,
    )


class RewindObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def completed(
        self,
        token: object,
        owner: str,
        origin: str,
        request_id: str,
        paths: tuple[RewindMutationPath, ...],
    ) -> None:
        record = await self.repository.prepare_rewind_mutation(
            mutation_request(token, owner, origin, request_id, paths)
        )
        await self.repository.complete_rewind_mutation(record.mutation_id)

    async def test_interleaved_messages_are_counted_only_for_thread(self) -> None:
        first = await self.repository.create_thread()
        second = await self.repository.create_thread()
        checkpoint = await self.repository.create_checkpoint(first, "bound")
        await self.repository.append_message(second, Message("user", "other"))
        await self.repository.append_message(first, Message("user", "one"))
        await self.repository.append_message(second, Message("assistant", "other"))
        await self.repository.append_message(first, Message("assistant", "two"))
        observed = await self.repository.observe_rewind(
            first, checkpoint, RewindReadLimits()
        )
        self.assertEqual(observed.conversation_message_count, 2)
        with self.assertRaises(SessionNotFound):
            await self.repository.observe_rewind(
                second, checkpoint, RewindReadLimits()
            )

    async def test_observation_orders_owner_and_foreign_mutations(self) -> None:
        owner = await self.repository.create_thread()
        foreign = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, foreign, foreign, "foreign", (path_fact("f.txt"),)
        )
        await self.completed(
            coverage.token, owner, owner, "owned", (path_fact("o.txt"),)
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        self.assertEqual(
            tuple(item.owner_thread_id for item in observed.mutations),
            (foreign, owner),
        )
        self.assertEqual(
            tuple(item.sequence for item in observed.mutations), (1, 2)
        )

    async def test_limits_return_no_partial_mutation_chain(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, owner, owner, "one", (path_fact("a.txt"),)
        )
        await self.completed(
            coverage.token,
            owner,
            owner,
            "two",
            (path_fact("b.txt"), path_fact("c.txt")),
        )
        mutation_limited = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits(1, 10)
        )
        path_limited = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits(10, 2)
        )
        self.assertTrue(mutation_limited.limit_exceeded)
        self.assertEqual(mutation_limited.mutations, ())
        self.assertTrue(path_limited.limit_exceeded)
        self.assertEqual(path_limited.mutations, ())

    async def test_heads_capture_later_message_mutation_and_invalidation(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        before = await self.repository.observe_rewind_heads(owner, FINGERPRINT)
        await self.repository.append_message(owner, Message("user", "later"))
        await self.completed(
            coverage.token, owner, owner, "write", (path_fact("a.txt"),)
        )
        await self.repository.record_rewind_gap(
            RewindGapPrepare(
                coverage.token,
                owner,
                owner,
                None,
                None,
                "gap",
                "run_command",
                "unknown-writer",
            )
        )
        after = await self.repository.observe_rewind_heads(owner, FINGERPRINT)
        self.assertGreater(after.message_sequence, before.message_sequence)
        self.assertGreater(after.mutation_sequence, before.mutation_sequence)
        self.assertEqual(after.coverage_state, RewindCoverageState.INVALIDATED)

    async def test_candidate_cursor_is_stable_and_facets_are_presence_only(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        plain = await self.repository.create_checkpoint(owner, "plain")
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        anchored = await self.repository.create_checkpoint(
            owner, "anchored", rewind_anchor=anchor
        )
        timestamp = "2026-07-17T01:02:03Z"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE checkpoints SET created_at = ? WHERE thread_id = ?",
                (timestamp, owner),
            )
        first = await self.repository.list_rewind_candidates(owner, limit=1)
        second = await self.repository.list_rewind_candidates(
            owner, cursor=first.next_cursor, limit=1
        )
        self.assertEqual(
            {first.items[0].checkpoint_id, second.items[0].checkpoint_id},
            {plain, anchored},
        )
        facets = {
            item.checkpoint_id: (item.has_message_bound, item.has_code_anchor)
            for item in first.items + second.items
        }
        self.assertEqual(facets[plain], (True, False))
        self.assertEqual(facets[anchored], (True, True))
        self.assertIsNone(second.next_cursor)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the observation tests and observe RED**

Run:

```powershell
& $python -m unittest src.code_agent.sessions.tests.test_rewind_anchors src.code_agent.sessions.tests.test_rewind_observations -v
```

Expected: tests error because `RewindSessionRepository` has no `get_rewind_checkpoint_anchor`, `observe_rewind`, `observe_rewind_heads`, or `list_rewind_candidates`.

- [ ] **Step 3: Implement focused checkpoint-write and observation-read mixins**

Create `src/code_agent/sessions/_rewind_checkpoints.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import uuid
from typing import Mapping

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import (
    decode_datetime,
    encode_datetime,
    encode_metadata,
    utc_now,
)
from ._records import _require_thread, _text, _thread_maximum, _touch_thread
from ._rewind_codec import decode_rewind_cursor, encode_rewind_cursor
from ._rewind_rows import (
    checkpoint_fact,
    checkpoint_record,
    coverage_record,
    mutation_record,
)
from .errors import SessionCorruptionError, SessionNotFound, SessionStorageError
from .rewind_models import (
    CoverageToken,
    MAX_REWIND_PAGE_SIZE,
    RewindCandidate,
    RewindCandidatePage,
    RewindCheckpointAnchor,
    RewindCoverageState,
    RewindObservation,
    RewindObservationHeads,
    RewindReadLimits,
)


class RewindCheckpointRepositoryMixin:
    _database: object

    async def get_rewind_checkpoint_anchor(
        self,
        coverage: CoverageToken,
        owner_thread_id: str,
    ) -> RewindCheckpointAnchor:
        if not isinstance(coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        owner_thread_id = _text(owner_thread_id, "owner_thread_id")

        def read(connection: sqlite3.Connection) -> RewindCheckpointAnchor:
            _require_thread(connection, owner_thread_id)
            row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (coverage.workspace_fingerprint,),
            ).fetchone()
            if row is None:
                raise SessionNotFound("rewind coverage not found")
            record = coverage_record(row)
            if record.token != coverage:
                raise SessionStorageError("rewind coverage generation moved")
            pending = connection.execute(
                "SELECT 1 FROM workspace_mutations "
                "WHERE workspace_fingerprint = ? AND status = 'prepared' LIMIT 1",
                (coverage.workspace_fingerprint,),
            ).fetchone()
            if pending is not None:
                raise SessionStorageError("prepared rewind mutation exists")
            return RewindCheckpointAnchor(
                coverage,
                owner_thread_id,
                record.state,
                record.mutation_high_water,
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
        *,
        rewind_anchor: RewindCheckpointAnchor | None = None,
    ) -> str:
        if rewind_anchor is None:
            return await super().create_checkpoint(thread_id, label, metadata)
        if not isinstance(rewind_anchor, RewindCheckpointAnchor):
            raise TypeError("rewind_anchor must be a RewindCheckpointAnchor")
        thread_id = _text(thread_id, "thread_id")
        label = _text(label, "label")
        data: Mapping[str, JSONValue] = {} if metadata is None else metadata
        validate_json_mapping(data, "metadata")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            _require_thread(connection, rewind_anchor.owner_thread_id)
            row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (rewind_anchor.coverage.workspace_fingerprint,),
            ).fetchone()
            if row is None:
                raise SessionNotFound("rewind coverage not found")
            current = coverage_record(row)
            expected = (
                rewind_anchor.coverage,
                rewind_anchor.coverage_state,
                rewind_anchor.mutation_sequence,
            )
            actual = (current.token, current.state, current.mutation_high_water)
            if actual != expected:
                raise SessionStorageError("rewind checkpoint anchor is stale")
            pending = connection.execute(
                "SELECT 1 FROM workspace_mutations "
                "WHERE workspace_fingerprint = ? AND status = 'prepared' LIMIT 1",
                (rewind_anchor.coverage.workspace_fingerprint,),
            ).fetchone()
            if pending is not None:
                raise SessionStorageError("prepared rewind mutation exists")
            message_sequence = _thread_maximum(connection, "messages", thread_id)
            event_sequence = _thread_maximum(connection, "events", thread_id)
            connection.execute(
                "INSERT INTO checkpoints("
                "id, thread_id, label, metadata, created_at, "
                "message_sequence, event_sequence"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    thread_id,
                    label,
                    encode_metadata(data),
                    timestamp,
                    message_sequence,
                    event_sequence,
                ),
            )
            connection.execute(
                "INSERT INTO checkpoint_rewind_facts("
                "checkpoint_id, owner_thread_id, workspace_fingerprint, "
                "coverage_generation, mutation_sequence, coverage_state, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    rewind_anchor.owner_thread_id,
                    rewind_anchor.coverage.workspace_fingerprint,
                    rewind_anchor.coverage.generation,
                    rewind_anchor.mutation_sequence,
                    rewind_anchor.coverage_state.value,
                    timestamp,
                ),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier
```

Create `src/code_agent/sessions/_rewind_observations.py` with this complete content:

```python
from __future__ import annotations

import sqlite3
import uuid
from typing import Mapping

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import (
    decode_datetime,
    encode_datetime,
    encode_metadata,
    utc_now,
)
from ._records import _require_thread, _text, _thread_maximum, _touch_thread
from ._rewind_codec import decode_rewind_cursor, encode_rewind_cursor
from ._rewind_rows import (
    checkpoint_fact,
    checkpoint_record,
    coverage_record,
    mutation_record,
)
from .errors import SessionCorruptionError, SessionNotFound, SessionStorageError
from .rewind_models import (
    CoverageToken,
    MAX_REWIND_PAGE_SIZE,
    RewindCandidate,
    RewindCandidatePage,
    RewindCheckpointAnchor,
    RewindCoverageState,
    RewindObservation,
    RewindObservationHeads,
    RewindReadLimits,
)


class RewindObservationRepositoryMixin:
    _database: object

    async def observe_rewind(
        self,
        thread_id: str,
        checkpoint_id: str,
        limits: RewindReadLimits,
    ) -> RewindObservation:
        thread_id = _text(thread_id, "thread_id")
        checkpoint_id = _text(checkpoint_id, "checkpoint_id")
        if not isinstance(limits, RewindReadLimits):
            raise TypeError("limits must be RewindReadLimits")

        def read(connection: sqlite3.Connection) -> RewindObservation:
            row = connection.execute(
                "SELECT c.*, f.owner_thread_id AS rewind_owner_thread_id, "
                "f.workspace_fingerprint, f.coverage_generation, "
                "f.mutation_sequence AS rewind_mutation_sequence, "
                "f.coverage_state AS rewind_coverage_state, "
                "f.created_at AS rewind_created_at "
                "FROM checkpoints AS c LEFT JOIN checkpoint_rewind_facts AS f "
                "ON f.checkpoint_id = c.id "
                "WHERE c.id = ? AND c.thread_id = ?",
                (checkpoint_id, thread_id),
            ).fetchone()
            if row is None:
                raise SessionNotFound("checkpoint not found")
            checkpoint = checkpoint_record(row)
            fact = checkpoint_fact(row)
            message_head = _thread_maximum(connection, "messages", thread_id)
            event_head = _thread_maximum(connection, "events", thread_id)
            count = _conversation_count(
                connection, thread_id, checkpoint.message_sequence, message_head
            )
            if fact is None:
                return RewindObservation(
                    checkpoint,
                    None,
                    count,
                    RewindObservationHeads(message_head, event_head, 0, None, None),
                    (),
                    False,
                )
            coverage_row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (fact.workspace_fingerprint,),
            ).fetchone()
            if coverage_row is None:
                raise SessionCorruptionError("checkpoint coverage is missing")
            coverage = coverage_record(coverage_row)
            mutation_head = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) "
                    "FROM workspace_mutations WHERE workspace_fingerprint = ?",
                    (fact.workspace_fingerprint,),
                ).fetchone()[0]
            )
            heads = RewindObservationHeads(
                message_head,
                event_head,
                mutation_head,
                coverage.generation,
                coverage.state,
            )
            mutation_rows = connection.execute(
                "SELECT * FROM workspace_mutations "
                "WHERE workspace_fingerprint = ? AND sequence > ? "
                "AND sequence <= ? ORDER BY sequence LIMIT ?",
                (
                    fact.workspace_fingerprint,
                    fact.mutation_sequence,
                    mutation_head,
                    limits.max_mutations + 1,
                ),
            ).fetchall()
            if len(mutation_rows) > limits.max_mutations:
                return RewindObservation(checkpoint, fact, count, heads, (), True)
            sequences = tuple(item["sequence"] for item in mutation_rows)
            path_rows: list[sqlite3.Row] = []
            if sequences:
                marks = ",".join("?" for _ in sequences)
                path_rows = connection.execute(
                    "SELECT * FROM workspace_mutation_paths "
                    f"WHERE mutation_sequence IN ({marks}) "
                    "ORDER BY mutation_sequence, ordinal LIMIT ?",
                    (*sequences, limits.max_paths + 1),
                ).fetchall()
            if len(path_rows) > limits.max_paths:
                return RewindObservation(checkpoint, fact, count, heads, (), True)
            grouped = {
                sequence: tuple(
                    item
                    for item in path_rows
                    if item["mutation_sequence"] == sequence
                )
                for sequence in sequences
            }
            mutations = tuple(
                mutation_record(item, grouped[item["sequence"]])
                for item in mutation_rows
            )
            return RewindObservation(
                checkpoint, fact, count, heads, mutations, False
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def observe_rewind_heads(
        self,
        thread_id: str,
        workspace_fingerprint: str,
    ) -> RewindObservationHeads:
        thread_id = _text(thread_id, "thread_id")
        fingerprint = CoverageToken(
            workspace_fingerprint, 1
        ).workspace_fingerprint

        def read(connection: sqlite3.Connection) -> RewindObservationHeads:
            _require_thread(connection, thread_id)
            message_head = _thread_maximum(connection, "messages", thread_id)
            event_head = _thread_maximum(connection, "events", thread_id)
            row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is None:
                return RewindObservationHeads(
                    message_head, event_head, 0, None, None
                )
            coverage = coverage_record(row)
            mutation_head = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) "
                    "FROM workspace_mutations WHERE workspace_fingerprint = ?",
                    (fingerprint,),
                ).fetchone()[0]
            )
            return RewindObservationHeads(
                message_head,
                event_head,
                mutation_head,
                coverage.generation,
                coverage.state,
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def list_rewind_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCandidatePage:
        thread_id = _text(thread_id, "thread_id")
        if type(limit) is not int or not 1 <= limit <= MAX_REWIND_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 100")
        decoded = None if cursor is None else decode_rewind_cursor(cursor)

        def read(connection: sqlite3.Connection) -> RewindCandidatePage:
            _require_thread(connection, thread_id)
            predicate = ""
            parameters: list[object] = [thread_id]
            if decoded is not None:
                predicate = (
                    "AND (c.created_at < ? OR "
                    "(c.created_at = ? AND c.id < ?)) "
                )
                parameters.extend((decoded[0], decoded[0], decoded[1]))
            parameters.append(limit + 1)
            rows = connection.execute(
                "SELECT c.id, c.label, c.created_at, c.message_sequence, "
                "f.checkpoint_id AS rewind_checkpoint_id "
                "FROM checkpoints AS c LEFT JOIN checkpoint_rewind_facts AS f "
                "ON f.checkpoint_id = c.id WHERE c.thread_id = ? "
                f"{predicate}ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
            visible = rows[:limit]
            try:
                items = tuple(
                    RewindCandidate(
                        item["id"],
                        item["label"],
                        decode_datetime(item["created_at"], "checkpoint"),
                        item["message_sequence"] is not None,
                        item["rewind_checkpoint_id"] is not None,
                    )
                    for item in visible
                )
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError(
                    "invalid rewind candidate"
                ) from error
            next_cursor = None
            if len(rows) > limit:
                last = visible[-1]
                next_cursor = encode_rewind_cursor(
                    last["created_at"], last["id"]
                )
            return RewindCandidatePage(items, next_cursor)

        return await self._database.read(read)  # type: ignore[attr-defined]


def _conversation_count(
    connection: sqlite3.Connection,
    thread_id: str,
    bound: int | None,
    head: int,
) -> int | None:
    if bound is None or bound > head:
        return None
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id = ? "
            "AND sequence > ? AND sequence <= ?",
            (thread_id, bound, head),
        ).fetchone()[0]
    )
```

- [ ] **Step 4: Compose observations into the narrow repository**

Replace the complete content of `src/code_agent/sessions/rewind_repository.py` with:

```python
from __future__ import annotations

from ._rewind_mutations import RewindMutationRepositoryMixin
from ._rewind_checkpoints import RewindCheckpointRepositoryMixin
from ._rewind_observations import RewindObservationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    RewindCheckpointRepositoryMixin,
    RewindObservationRepositoryMixin,
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
```

- [ ] **Step 5: Run focused GREEN checks**

Run:

```powershell
& $python -m unittest src.code_agent.sessions.tests.test_rewind_anchors src.code_agent.sessions.tests.test_rewind_observations -v
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_checkpoint_bounds.py' -v
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_mutations.py' -v
& $python -m compileall -q src/code_agent/sessions
```

Expected: all selected tests pass. Conversation count is 2 despite interleaved global sequences; a child checkpoint exposes the child only as conversation scope and root only as code-owner scope; both overflow cases return an empty chain with `limit_exceeded=True`.

- [ ] **Step 6: Commit the observation slice**

Run:

```powershell
git diff --check
git add -- src/code_agent/sessions/_rewind_checkpoints.py src/code_agent/sessions/_rewind_observations.py src/code_agent/sessions/rewind_repository.py src/code_agent/sessions/tests/test_rewind_anchors.py src/code_agent/sessions/tests/test_rewind_observations.py
git diff --cached --name-only
git commit -m "锚定回溯检查点：提供原子有界观测"
```

Expected staged allowlist: exactly the five paths above; commit succeeds.

## Task 5: Update the Sessions contract and run the full Feature suite

**Files:**
- Modify: `src/code_agent/sessions/AGENTS.md`

- [ ] **Step 1: Replace only the rewind Units at the end of the contract**

In `src/code_agent/sessions/AGENTS.md`, keep the title, goal, boundary sections, and all existing non-rewind Units unchanged. Replace the final rewind boundary summary in `## Units` by appending these exact three Unit entries:

```markdown
- `CoverageToken`、`RewindMutationPrepare`、`RewindGapPrepare`、`RewindObservation`: 表达深度冻结且有界的 coverage、mutation、checkpoint 与 observation 事实 | 无副作用 | SnapshotHandle 仅作为 canonical JSON；checkpoint conversation thread 与 code owner scope 分离
- `RewindSessionRepository.ensure_rewind_coverage`、`prepare_rewind_mutation`、`complete_rewind_mutation`、`abort_rewind_mutation`、`record_rewind_gap`: 原子持久化 workspace coverage 与 mutation 状态机 | SQLite I/O | mutation ID 由 Sessions 生成；幂等键为 workspace/origin/request；失效 coverage 仅接受 durable repeat gap
- `RewindSessionRepository.get_rewind_checkpoint_anchor`、`create_checkpoint`、`observe_rewind`、`observe_rewind_heads`、`list_rewind_candidates`: 原子锚定 checkpoint 并提供线程过滤、游标分页和预算限制的只读事实 | SQLite I/O | 旧或未锚定 checkpoint 无 code fact；任何 limit overflow 均不返回部分 mutation chain
```

- [ ] **Step 2: Verify every changed/created Python file stays within 300 lines**

Run:

```powershell
$paths = @(
  'src/code_agent/sessions/_rewind_model_base.py',
  'src/code_agent/sessions/_rewind_model_records.py',
  'src/code_agent/sessions/rewind_models.py',
  'src/code_agent/sessions/_rewind_codec.py',
  'src/code_agent/sessions/_rewind_schema.py',
  'src/code_agent/sessions/_rewind_rows.py',
  'src/code_agent/sessions/_rewind_mutation_sql.py',
  'src/code_agent/sessions/_rewind_mutations.py',
  'src/code_agent/sessions/_rewind_checkpoints.py',
  'src/code_agent/sessions/_rewind_observations.py',
  'src/code_agent/sessions/rewind_repository.py',
  'src/code_agent/sessions/_database.py',
  'src/code_agent/sessions/tests/test_rewind_models.py',
  'src/code_agent/sessions/tests/test_rewind_migrations.py',
  'src/code_agent/sessions/tests/test_rewind_mutations.py',
  'src/code_agent/sessions/tests/test_rewind_anchors.py',
  'src/code_agent/sessions/tests/test_rewind_observations.py'
)
$tooLarge = $paths | Where-Object { (Get-Content -LiteralPath $_).Count -gt 300 }
if ($tooLarge) { throw "files exceed 300 lines: $tooLarge" }
```

Expected: command exits without output. Do not solve an overage by compressing statements; split private helpers into a focused file and rerun the same command.

- [ ] **Step 3: Run the complete Sessions suite**

Run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Sessions suite failed' }
& $python -m compileall -q src/code_agent/sessions
if ($LASTEXITCODE -ne 0) { throw 'Sessions compileall failed' }
git diff --check
```

Expected: every Sessions test passes, compileall exits 0, and `git diff --check` reports no whitespace errors.

- [ ] **Step 4: Prove the stage and file boundary**

Run:

```powershell
$outside = git status --short | Where-Object {
  $_ -notmatch '^\?\? src/code_agent/sessions/' -and
  $_ -notmatch '^ M src/code_agent/sessions/'
}
if ($outside) { throw "out-of-stage changes detected: $outside" }
git diff -- src/code_agent/sessions/repository.py src/code_agent/sessions/_records.py
```

Expected: `$outside` is empty and the final `git diff` prints nothing. No Workspace, Core, Interfaces, integration, root config, or legacy repository file changed.

- [ ] **Step 5: Commit only the Sessions contract**

Run:

```powershell
git add -- src/code_agent/sessions/AGENTS.md
git diff --cached --name-only
git commit -m "更新会话契约：记录可信回溯单元"
```

Expected staged allowlist: exactly `src/code_agent/sessions/AGENTS.md`; commit succeeds.

## Final execution audit

Before handing this Feature to Workspace/Core/Interfaces/integration workers, run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
& $python -m compileall -q src/code_agent/sessions
git diff --check
git status --short
```

Expected: the full Sessions suite passes, compilation succeeds, whitespace check is clean, and the worktree has no uncommitted files from this plan. The four implementation commits and one Units commit are:

1. `新增回溯模型：冻结可信记录与游标`
2. `迁移会话存储：建立空白回溯日志表`
3. `持久化回溯变更：实现原子日志状态机`
4. `锚定回溯检查点：提供原子有界观测`
5. `更新会话契约：记录可信回溯单元`
