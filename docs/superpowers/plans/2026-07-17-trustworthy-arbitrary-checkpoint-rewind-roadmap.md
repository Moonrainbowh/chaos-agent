# Trustworthy Arbitrary-Checkpoint Rewind Delivery Roadmap

> **Planning status:** This cross-subsystem document is an architectural
> delivery map, not a worker-executable implementation plan. Before each stage,
> write and review a separate code-complete implementation plan for that single
> Feature or integration slice. Execute only the reviewed child plan; use this
> roadmap to preserve ordering, trust boundaries, and acceptance coverage.

**Goal:** Add trustworthy, preview-only conversation/code/both rewind for every checkpoint created under a complete mutation-capture chain.

**Architecture:** Sessions v11 persists coverage, prepared/completed mutations, path preimages, and checkpoint mutation high-water marks. Workspace exposes deterministic file states; Core explicitly propagates action lineage; Windows integration serializes mutation/checkpoint ordering with a separate cross-process SQLite gate and builds verified observations; Interfaces renders immutable read-only previews through `/rewind`.

**Tech Stack:** Python 3.10+, `asyncio`, SQLite WAL/FULL durability, guarded workspace byte snapshots, `unittest`, Windows Terminal TUI.

---

## References and execution order

- Approved design:
  `docs/superpowers/specs/2026-07-17-trustworthy-arbitrary-checkpoint-rewind-design.md`
- Parent roadmap:
  `docs/superpowers/plans/2026-07-16-cli-tui-p0-recovery-review-plan.md`
- Isolated worktree:
  `C:\tmp\chaos4-cli-tui-roadmap`
- Branch:
  `codex/cli-tui-roadmap`

Deliver Tasks 0 through 9 strictly in order. Tasks 1 through 6 are Feature
implementation stages and may modify only their named `src/code_agent/<feature>`
directory. Tasks 7 through 9 are integration stages and must not modify `src/`.

Child implementation plans:

- Requirements boundary:
  `docs/superpowers/plans/2026-07-17-rewind-requirements-boundary.md`
- Sessions Feature:
  `docs/superpowers/plans/2026-07-17-rewind-sessions-feature.md`
- Workspace Feature: create after Sessions passes.
- Core Feature: create after Workspace passes.
- Interfaces Feature: create after Core passes.
- Windows integration: create after all Feature suites pass.

Use this test prefix in every PowerShell test step:

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
$python = '.\.venv\Scripts\python.exe'
```

## File responsibility map

| File | Responsibility |
|---|---|
| `src/code_agent/sessions/rewind_models.py` | Frozen persisted rewind records and strict invariants |
| `src/code_agent/sessions/_rewind_codec.py` | Canonical bounded handle/cursor encoding |
| `src/code_agent/sessions/_rewind_schema.py` | v11 SQL migration and required-column declarations |
| `src/code_agent/sessions/_rewind_mutations.py` | Coverage and mutation state transitions |
| `src/code_agent/sessions/_rewind_observations.py` | Atomic checkpoint anchors, observations, heads, and pagination |
| `src/code_agent/sessions/rewind_repository.py` | Narrow repository subclass composing rewind-only mixins |
| `src/code_agent/workspace/rewind_state.py` | Deterministic pre/post/current path state and relevant-path digest |
| `src/code_agent/core/action_execution.py` | Explicit action owner/origin/task/request lineage |
| `src/code_agent/interfaces/rewind_models.py` | Pure preview and candidate models |
| `src/code_agent/interfaces/rewind_view.py` | Pure projection and bounded safe rendering |
| `src/code_agent/interfaces/tui_rewind_commands.py` | Read-only `/rewind` argument handling and delegation |
| `code_agent_win/rewind_gate.py` | Cross-process workspace mutation/checkpoint lock |
| `code_agent_win/rewind_capture.py` | Known-edit two-phase capture and unknown-writer invalidation |
| `code_agent_win/rewind_sessions.py` | Checkpoint-ordering repository wrapper |
| `code_agent_win/rewind_runtime.py` | Snapshot/chain/current-state validation and as-of retry |

## Task 0: Freeze Feature requirement boundaries

**Stage:** Requirements

**Files:**
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`

- [ ] **Step 1: Add only goal/boundary clauses**

Add these clauses without changing any `## Units` entry:

```markdown
Sessions:
- 负责：以专用表持久化 workspace coverage、prepared/completed mutation、path preimage 和 checkpoint mutation 高水位，并提供同事务的有界 rewind observation。
- 不负责：解析 SnapshotHandle、读取工作区、判断当前路径冲突或把 metadata 当作可信 rewind 事实。

Workspace:
- 负责：为已规划的 typed edit 生成精确 bytes/existence 前镜像、确定性前后 hash、当前路径状态与相关路径摘要。
- 不负责：Action 归因、checkpoint 排序、Sessions journal 或 rewind 可用性裁决。

Core:
- 负责：把 owner/origin thread、task、request 和 parent request 作为不可变 Action execution context 显式传给 dispatcher。
- 不负责：工作区快照、mutation journal、coverage 或 rewind UI。

Interfaces:
- 负责：纯 rewind 模型、稳定禁用原因、候选分页、只读 source 委托和有界安全渲染。
- 不负责：Sessions 查询、snapshot 加载、文件恢复、Git 操作、apply 授权或 provider 调用。
```

- [ ] **Step 2: Verify the requirement-stage diff**

Run:

```powershell
git diff -- src/code_agent/sessions/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md
git diff --check
```

Expected: only boundary text changes; no production file, test file, or Units
change.

- [ ] **Step 3: Commit the requirements**

```powershell
git add src/code_agent/sessions/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md
git commit -m "明确回溯需求：冻结跨 Feature 信任边界"
```

## Task 1: Add Sessions rewind models and schema v11

**Stage:** Sessions Feature implementation

**Files:**
- Create: `src/code_agent/sessions/rewind_models.py`
- Create: `src/code_agent/sessions/_rewind_codec.py`
- Create: `src/code_agent/sessions/_rewind_schema.py`
- Modify: `src/code_agent/sessions/_database.py`
- Create: `src/code_agent/sessions/rewind_repository.py`
- Create: `src/code_agent/sessions/tests/test_rewind_models.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`

- [ ] **Step 1: Write failing frozen-model tests**

Create tests covering stable values and invalid state:

```python
def test_rewind_enums_have_stable_values(self) -> None:
    self.assertEqual(RewindCoverageState.ACTIVE.value, "active")
    self.assertEqual(RewindCoverageState.INVALIDATED.value, "invalidated")
    self.assertEqual(RewindMutationStatus.PREPARED.value, "prepared")
    self.assertEqual(RewindMutationStatus.COMPLETED.value, "completed")
    self.assertEqual(RewindMutationStatus.ABORTED.value, "aborted")
    self.assertEqual(RewindMutationStatus.GAP.value, "gap")

def test_path_fact_rejects_inconsistent_hashes(self) -> None:
    with self.assertRaises(ValueError):
        RewindMutationPath(
            "note.txt",
            before_existed=False,
            before_sha256="0" * 64,
            baseline=RewindBaseline.ABSENT,
            after_existed=True,
            after_sha256="1" * 64,
        )

def test_snapshot_handle_is_deep_frozen(self) -> None:
    source = {"identifier": "a" * 32, "paths": ["note.txt"]}
    request = RewindMutationPrepare(
        CoverageToken("b" * 64, 1),
        "owner",
        "origin",
        None,
        None,
        "request",
        "write_file",
        source,
        (
            RewindMutationPath(
                "note.txt", False, None, RewindBaseline.ABSENT, True, "1" * 64
            ),
        ),
    )
    source["paths"].append("changed.txt")
    self.assertEqual(request.snapshot_handle["paths"], ("note.txt",))

def test_checkpoint_fact_separates_conversation_thread_from_code_owner(
    self,
) -> None:
    created_at = datetime(2026, 7, 17, tzinfo=timezone.utc)
    fact = RewindCheckpointFact(
        checkpoint_id="checkpoint-child",
        owner_thread_id="thread-root",
        coverage=CoverageToken("a" * 64, 1),
        mutation_sequence=7,
        coverage_state=RewindCoverageState.ACTIVE,
        created_at=created_at,
    )
    self.assertEqual(fact.checkpoint_id, "checkpoint-child")
    self.assertEqual(fact.owner_thread_id, "thread-root")
    self.assertEqual(fact.coverage.workspace_fingerprint, "a" * 64)
    self.assertEqual(fact.mutation_sequence, 7)
```

- [ ] **Step 2: Run the model tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_models.py' -v
```

Expected: import failure for `code_agent.sessions.rewind_models`.

- [ ] **Step 3: Implement strict frozen records**

Define these exact enums and records in `rewind_models.py`:

```python
MAX_REWIND_ACTION_PATHS = 32
MAX_REWIND_HANDLE_BYTES = 64 * 1024
MAX_REWIND_TEXT_FIELD = 512
DEFAULT_REWIND_MAX_MUTATIONS = 256
DEFAULT_REWIND_MAX_PATHS = 512
MAX_REWIND_PAGE_SIZE = 100

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

@dataclass(frozen=True)
class CoverageToken:
    workspace_fingerprint: str
    generation: int

@dataclass(frozen=True)
class RewindMutationPath:
    path: str
    before_existed: bool
    before_sha256: str | None
    baseline: RewindBaseline
    after_existed: bool
    after_sha256: str | None

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

@dataclass(frozen=True)
class RewindCoverageRecord:
    token: CoverageToken
    state: RewindCoverageState
    mutation_high_water: int
    invalidation_reason: str | None

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

@dataclass(frozen=True)
class RewindCheckpointAnchor:
    coverage: CoverageToken
    owner_thread_id: str
    coverage_state: RewindCoverageState
    mutation_sequence: int

@dataclass(frozen=True)
class RewindCheckpointFact:
    checkpoint_id: str
    owner_thread_id: str
    coverage: CoverageToken
    mutation_sequence: int
    coverage_state: RewindCoverageState
    created_at: datetime

@dataclass(frozen=True)
class RewindReadLimits:
    max_mutations: int = DEFAULT_REWIND_MAX_MUTATIONS
    max_paths: int = DEFAULT_REWIND_MAX_PATHS
```

Also define the exact observation and candidate records shown below. Validate
non-blank IDs, lowercase 64-hex hashes/fingerprints, positive generations,
non-negative sequences, unique canonical relative paths, consistent
existence/hash pairs, bounded limits, timezone-aware UTC datetimes, and
deep-frozen JSON. `RewindCoverageRecord` exposes read-only
`workspace_fingerprint` and `generation` properties delegated to its token.
Prepared/completed/aborted records require a non-null handle and at least one
path; gap records require a non-null reason, null handle, and no paths.

Sessions generates `mutation_id = uuid.uuid4().hex`; callers never choose it.
The composite `(workspace_fingerprint, origin_thread_id, request_id)` is the
idempotency key. Repeating an identical canonical prepare or gap request returns
the existing record. Reusing that key with any different action, owner, task,
handle, path, or reason raises `SessionStorageError` and changes no row.

Canonical-JSON encode snapshot handles before accepting them and reject values
over `MAX_REWIND_HANDLE_BYTES`. The current built-in typed edits use one path,
but the persisted contract remains safely bounded for future typed actions.

Implement the codec without accepting arbitrary Python objects:

```python
def encode_rewind_handle(value: Mapping[str, JSONValue]) -> str:
    validate_json_mapping(value, "snapshot_handle")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_REWIND_HANDLE_BYTES:
        raise ValueError("snapshot_handle exceeds its encoded byte limit")
    return encoded

def decode_rewind_handle(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, str):
        raise SessionCorruptionError("invalid rewind snapshot handle")
    try:
        decoded = json.loads(value)
        validate_json_mapping(decoded, "snapshot_handle")
        return freeze_mapping(decoded, "snapshot_handle")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid rewind snapshot handle") from error

def encode_rewind_cursor(created_at: str, checkpoint_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at, "id": checkpoint_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

def decode_rewind_cursor(value: str) -> tuple[str, str]:
    try:
        if not isinstance(value, str) or not value or "=" in value:
            raise ValueError
        if len(value) > 2 * MAX_REWIND_TEXT_FIELD:
            raise ValueError
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or set(decoded) != {"created_at", "id"}:
            raise ValueError
        created_at = decoded["created_at"]
        checkpoint_id = decoded["id"]
        if (
            not isinstance(created_at, str)
            or not created_at.strip()
            or len(created_at) > MAX_REWIND_TEXT_FIELD
            or not isinstance(checkpoint_id, str)
            or not checkpoint_id.strip()
            or len(checkpoint_id) > MAX_REWIND_TEXT_FIELD
        ):
            raise ValueError
        if encode_rewind_cursor(created_at, checkpoint_id) != value:
            raise ValueError
        return created_at, checkpoint_id
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        ValueError,
    ) as error:
        raise ValueError("invalid rewind cursor") from error
```

Use these exact observation shapes so later runtime tasks do not rename fields:

```python
@dataclass(frozen=True)
class RewindObservationHeads:
    message_sequence: int
    event_sequence: int
    mutation_sequence: int
    coverage_generation: int | None
    coverage_state: RewindCoverageState | None

@dataclass(frozen=True)
class RewindObservation:
    checkpoint: CheckpointRecord
    checkpoint_fact: RewindCheckpointFact | None
    conversation_message_count: int | None
    heads: RewindObservationHeads
    mutations: tuple[RewindMutationRecord, ...]
    limit_exceeded: bool

@dataclass(frozen=True)
class RewindCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

@dataclass(frozen=True)
class RewindCandidatePage:
    items: tuple[RewindCandidate, ...]
    next_cursor: str | None
```

- [ ] **Step 4: Add failing v10-to-v11 migration tests**

Add:

```python
def test_v10_migrates_to_v11_without_checkpoint_rewind_facts(self) -> None:
    create_v3_database(self.database)
    advance_v3_database(self.database, 10)
    SQLiteSessionRepository(self.database)
    with sqlite3.connect(self.database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM checkpoint_rewind_facts"
        ).fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    self.assertEqual(version, 11)
    self.assertEqual(count, 0)

def test_v11_missing_rewind_table_fails_schema_check(self) -> None:
    create_v3_database(self.database)
    advance_v3_database(self.database, 10)
    with sqlite3.connect(self.database) as connection:
        connection.execute("PRAGMA user_version = 11")
    with self.assertRaises(SessionCorruptionError):
        SQLiteSessionRepository(self.database)
```

- [ ] **Step 5: Run migration tests to verify RED**

```powershell
& $python -m unittest src.code_agent.sessions.tests.test_migrations -v
```

Expected: schema version/table assertions fail.

- [ ] **Step 6: Implement the isolated v11 schema**

Put the complete v11 SQL in `_rewind_schema.py`:

```sql
CREATE TABLE workspace_rewind_coverage (
  workspace_fingerprint TEXT PRIMARY KEY,
  generation INTEGER NOT NULL CHECK(generation > 0),
  state TEXT NOT NULL CHECK(state IN ('active','invalidated')),
  mutation_high_water INTEGER NOT NULL DEFAULT 0 CHECK(mutation_high_water >= 0),
  invalidation_reason TEXT,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (state = 'active' AND invalidation_reason IS NULL) OR
    (state = 'invalidated' AND invalidation_reason IS NOT NULL)
  )
);
CREATE TABLE workspace_mutations (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  mutation_id TEXT NOT NULL UNIQUE,
  workspace_fingerprint TEXT NOT NULL
    REFERENCES workspace_rewind_coverage(workspace_fingerprint),
  coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0),
  owner_thread_id TEXT NOT NULL REFERENCES threads(id),
  origin_thread_id TEXT NOT NULL REFERENCES threads(id),
  task_id TEXT REFERENCES tasks(id),
  parent_request_id TEXT,
  request_id TEXT NOT NULL,
  action_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('prepared','completed','aborted','gap')),
  gap_reason TEXT,
  snapshot_handle TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(workspace_fingerprint, origin_thread_id, request_id)
);
CREATE TABLE workspace_mutation_paths (
  mutation_sequence INTEGER NOT NULL
    REFERENCES workspace_mutations(sequence) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  path TEXT NOT NULL,
  pre_existed INTEGER NOT NULL CHECK(pre_existed IN (0,1)),
  pre_sha256 TEXT,
  baseline TEXT NOT NULL CHECK(
    baseline IN (
      'git-staged','git-unstaged','git-untracked',
      'non-git-existing','absent','unknown'
    )
  ),
  post_existed INTEGER NOT NULL CHECK(post_existed IN (0,1)),
  post_sha256 TEXT,
  PRIMARY KEY(mutation_sequence, ordinal),
  UNIQUE(mutation_sequence, path)
);
CREATE TABLE checkpoint_rewind_facts (
  checkpoint_id TEXT PRIMARY KEY REFERENCES checkpoints(id) ON DELETE CASCADE,
  owner_thread_id TEXT NOT NULL REFERENCES threads(id),
  workspace_fingerprint TEXT NOT NULL
    REFERENCES workspace_rewind_coverage(workspace_fingerprint),
  coverage_generation INTEGER NOT NULL CHECK(coverage_generation > 0),
  mutation_sequence INTEGER NOT NULL CHECK(mutation_sequence >= 0),
  coverage_state TEXT NOT NULL CHECK(coverage_state IN ('active','invalidated')),
  created_at TEXT NOT NULL
);
CREATE INDEX workspace_mutations_workspace_sequence
  ON workspace_mutations(workspace_fingerprint, sequence);
CREATE INDEX workspace_mutations_owner_sequence
  ON workspace_mutations(owner_thread_id, sequence);
CREATE INDEX workspace_mutation_paths_path_sequence
  ON workspace_mutation_paths(path, mutation_sequence);
CREATE INDEX checkpoint_rewind_facts_owner
  ON checkpoint_rewind_facts(owner_thread_id, mutation_sequence);
```

Export `REWIND_MIGRATION` and `REWIND_REQUIRED_COLUMNS`, import them into
`_database.py`, set `SCHEMA_VERSION = 11`, and merge them into the existing
migration/required-column maps.

Create the bounded extension point without modifying the 373-line legacy
repository:

```python
from .repository import SQLiteSessionRepository


class RewindSessionRepository(SQLiteSessionRepository):
    """Repository extension point for trusted rewind-only persistence."""
```

- [ ] **Step 7: Verify GREEN and commit**

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_models.py' -v
& $python -m unittest src.code_agent.sessions.tests.test_migrations -v
git diff --check
git add src/code_agent/sessions/rewind_models.py src/code_agent/sessions/_rewind_codec.py src/code_agent/sessions/_rewind_schema.py src/code_agent/sessions/_database.py src/code_agent/sessions/rewind_repository.py src/code_agent/sessions/tests/test_rewind_models.py src/code_agent/sessions/tests/test_migrations.py
git commit -m "新增回溯存储：建立 v11 日志结构"
```

Expected: all selected tests pass.

## Task 2: Persist the Sessions mutation lifecycle

**Stage:** Sessions Feature implementation

**Files:**
- Create: `src/code_agent/sessions/_rewind_mutations.py`
- Modify: `src/code_agent/sessions/rewind_repository.py`
- Create: `src/code_agent/sessions/tests/test_rewind_mutations.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover the durable state machine:

```python
async def test_gap_invalidates_coverage_and_ensure_never_reenables_it(self) -> None:
    owner = await self.repository.create_thread()
    coverage = await self.repository.ensure_rewind_coverage("a" * 64)
    gap = await self.repository.record_rewind_gap(
        RewindGapPrepare(
            coverage.token,
            owner,
            owner,
            None,
            None,
            "gap-request",
            "run_command",
            "unknown-workspace-writer",
        )
    )
    again = await self.repository.ensure_rewind_coverage("a" * 64)
    self.assertEqual(gap.status, RewindMutationStatus.GAP)
    self.assertEqual(again.state, RewindCoverageState.INVALIDATED)
    self.assertEqual(again.generation, coverage.generation)

async def test_complete_requires_the_prepared_record(self) -> None:
    with self.assertRaises(SessionNotFound):
        await self.repository.complete_rewind_mutation(999)

async def test_repeated_gap_is_recorded_without_reenabling_coverage(self) -> None:
    owner = await self.repository.create_thread()
    first = await self.repository.ensure_rewind_coverage("a" * 64)
    await self.repository.record_rewind_gap(
        RewindGapPrepare(
            first.token,
            owner,
            owner,
            None,
            None,
            "gap-1",
            "run_command",
            "unknown-writer",
        )
    )
    invalidated = await self.repository.ensure_rewind_coverage("a" * 64)
    second = await self.repository.record_rewind_gap(
        RewindGapPrepare(
            invalidated.token,
            owner,
            owner,
            None,
            None,
            "gap-2",
            "write_file",
            "coverage-already-invalidated",
        )
    )
    after = await self.repository.ensure_rewind_coverage("a" * 64)
    self.assertEqual(second.status, RewindMutationStatus.GAP)
    self.assertEqual(after.state, RewindCoverageState.INVALIDATED)
    self.assertEqual(after.generation, first.generation)
```

Create these additional exact test methods:

- `test_ensure_coverage_is_idempotent`: two calls return equal active records
  with generation `1`.
- `test_prepare_retains_owner_origin_and_task`: the loaded record equals every
  supplied identity field, including `parent_request_id`.
- `test_identical_prepare_is_idempotent`: a repeated canonical request returns
  the same mutation ID and sequence.
- `test_reused_origin_request_with_different_payload_is_rejected`: the second
  call raises `SessionStorageError` and table counts stay unchanged.
- `test_identical_gap_is_idempotent`: a repeated canonical gap returns the same
  mutation ID and sequence without changing the original invalidation reason.
- `test_failed_path_insert_rolls_back_prepare`: install a test-only SQLite
  trigger that raises `ABORT` when `NEW.ordinal = 1`, submit two distinct valid
  paths, and assert no mutation/path row or high-water change remains.
- `test_complete_and_abort_only_accept_prepared`: completed-to-aborted,
  aborted-to-completed, and gap transitions raise `SessionStorageError`.
- `test_snapshot_handle_metadata_cannot_forge_status`: caller JSON fields named
  `status`, `sequence`, and `generation` remain opaque handle content.
- `test_invalidated_coverage_rejects_complete_prepare`: prepare raises
  `SessionStorageError`, but `record_rewind_gap` remains accepted as specified
  above.

- [ ] **Step 2: Run lifecycle tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_mutations.py' -v
```

Expected: repository methods are missing.

- [ ] **Step 3: Implement transactional mutation methods**

Create `RewindMutationRepositoryMixin` with:

```python
async def ensure_rewind_coverage(
    self, workspace_fingerprint: str
) -> RewindCoverageRecord

async def prepare_rewind_mutation(
    self, request: RewindMutationPrepare
) -> RewindMutationRecord

async def complete_rewind_mutation(
    self, sequence: int
) -> RewindMutationRecord

async def abort_rewind_mutation(
    self, sequence: int
) -> RewindMutationRecord

async def record_rewind_gap(
    self, request: RewindGapPrepare
) -> RewindMutationRecord
```

`ensure_rewind_coverage` uses `INSERT OR IGNORE` with generation `1`, then reads
the persisted row; it never updates an invalidated row. Prepare verifies the
current active token, inserts one mutation plus all path rows, and advances
`mutation_high_water` in one write transaction. Complete uses
`UPDATE workspace_mutations SET status='completed', completed_at=? WHERE
sequence=? AND status='prepared'`; abort uses the equivalent `aborted` update.
Each transition requires exactly one changed row. Gap accepts a matching token
in either `active` or `invalidated` state. For `active`, it inserts the row and
changes coverage to `invalidated` in one transaction before execution. For
`invalidated`, it inserts another idempotent audit gap, preserves the same
generation/reason/state, and never re-enables coverage.

Use these exact transaction predicates:

```sql
SELECT *
FROM workspace_rewind_coverage
WHERE workspace_fingerprint = ?;

INSERT INTO workspace_mutations(
  mutation_id, workspace_fingerprint, coverage_generation,
  owner_thread_id, origin_thread_id, task_id, parent_request_id,
  request_id, action_name,
  status, gap_reason, snapshot_handle, created_at, completed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, ?, ?, NULL);

UPDATE workspace_rewind_coverage
SET mutation_high_water = ?, updated_at = ?
WHERE workspace_fingerprint = ?
  AND generation = ?
  AND state = 'active'
  AND mutation_high_water < ?;
```

Prepare requires the coverage select to equal the request token and be active;
the high-water update must change one row. Gap inserts the same mutation columns
with status `gap`, a non-null reason, and null handle, then runs:

```sql
UPDATE workspace_rewind_coverage
SET state = 'invalidated',
    invalidation_reason = COALESCE(invalidation_reason, ?),
    mutation_high_water = ?,
    updated_at = ?
WHERE workspace_fingerprint = ? AND generation = ?;
```

That update accepts both current states but must change one matching-generation
row. On composite idempotency-key collision, load the existing mutation and
ordered paths in the same transaction, compare its canonical request
fingerprint, and either return it unchanged or raise `SessionStorageError`.
That fingerprint includes `parent_request_id`.

Do not modify the already oversized legacy `repository.py`. Compose the new
behavior in the narrow public subclass created in Task 1:

```python
from ._rewind_mutations import RewindMutationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
```

- [ ] **Step 4: Verify GREEN and commit**

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_mutations.py' -v
git diff --check
git add src/code_agent/sessions/_rewind_mutations.py src/code_agent/sessions/repository.py src/code_agent/sessions/tests/test_rewind_mutations.py
git commit -m "持久化回溯变更：实现两阶段日志状态机"
```

## Task 3: Anchor checkpoints and observe bounded rewind facts

**Stage:** Sessions Feature implementation

**Files:**
- Create: `src/code_agent/sessions/_rewind_observations.py`
- Modify: `src/code_agent/sessions/rewind_repository.py`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Create: `src/code_agent/sessions/tests/test_rewind_observation.py`
- Modify: `src/code_agent/sessions/tests/test_checkpoint_bounds.py`

- [ ] **Step 1: Write failing anchor/observation tests**

Add tests proving the transaction boundary:

```python
async def test_interleaved_message_sequences_are_counted_by_thread(self) -> None:
    first = await self.repository.create_thread()
    second = await self.repository.create_thread()
    coverage = await self.repository.ensure_rewind_coverage("a" * 64)
    anchor = await self.repository.get_rewind_checkpoint_anchor(
        coverage.token, first
    )
    checkpoint = await self.repository.create_checkpoint(
        first, "anchor", rewind_anchor=anchor
    )
    await self.repository.append_message(second, Message("user", "other"))
    await self.repository.append_message(first, Message("user", "one"))
    await self.repository.append_message(second, Message("assistant", "other"))
    observed = await self.repository.observe_rewind(
        first, checkpoint, RewindReadLimits(100, 100)
    )
    self.assertEqual(observed.conversation_message_count, 1)

async def test_cross_thread_checkpoint_is_not_disclosed(self) -> None:
    first = await self.repository.create_thread()
    second = await self.repository.create_thread()
    checkpoint = await self.repository.create_checkpoint(first, "private")
    with self.assertRaises(SessionNotFound):
        await self.repository.observe_rewind(
            second, checkpoint, RewindReadLimits(100, 100)
        )

async def test_child_checkpoint_persists_root_code_owner_scope(self) -> None:
    root_thread = await self.repository.create_thread()
    child_thread = await self.repository.create_thread()
    coverage = await self.repository.ensure_rewind_coverage("a" * 64)
    anchor = await self.repository.get_rewind_checkpoint_anchor(
        coverage.token, root_thread
    )
    checkpoint_id = await self.repository.create_checkpoint(
        child_thread, "child checkpoint", rewind_anchor=anchor
    )
    observation = await self.repository.observe_rewind(
        child_thread,
        checkpoint_id,
        RewindReadLimits(100, 100),
    )
    self.assertEqual(observation.checkpoint.thread_id, child_thread)
    self.assertIsNotNone(observation.checkpoint_fact)
    assert observation.checkpoint_fact is not None
    self.assertEqual(
        observation.checkpoint_fact.owner_thread_id,
        root_thread,
    )
```

Create these additional exact test methods:

- `test_stale_anchor_generation_or_high_water_is_rejected`
- `test_anchor_rejects_any_prepared_mutation`
- `test_invalidated_anchor_persists_invalidated_fact`
- `test_unanchored_checkpoint_has_no_code_fact`
- `test_metadata_cannot_forge_checkpoint_rewind_fact`
- `test_observation_orders_owner_and_foreign_mutations_by_sequence`
- `test_mutation_limit_returns_empty_chain_and_limit_flag`
- `test_path_limit_returns_empty_chain_and_limit_flag`
- `test_observe_heads_detects_later_message_mutation_and_invalidation`
- `test_candidate_cursor_is_stable_for_equal_timestamps`
- `test_candidate_facets_report_presence_not_availability`

- [ ] **Step 2: Run observation tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_rewind_observation.py' -v
```

Expected: anchor/observation methods or keyword are missing.

- [ ] **Step 3: Implement anchor-aware checkpoint creation**

Override `create_checkpoint` in `RewindObservationRepositoryMixin` with:

```python
async def create_checkpoint(
    self,
    thread_id: str,
    label: str,
    metadata: Mapping[str, JSONValue] | None = None,
    *,
    rewind_anchor: RewindCheckpointAnchor | None = None,
) -> str:
```

Within the existing checkpoint write transaction:

1. compute thread message/event maxima;
2. when an anchor is present, reread coverage and require exact fingerprint,
   generation, state, and mutation high-water, and require the anchor's
   `owner_thread_id` to name an existing thread;
3. reject any `prepared` mutation for that workspace;
4. insert `checkpoints`;
5. insert `checkpoint_rewind_facts`;
6. touch the thread and commit.

Generic calls without an anchor remain compatible and create no trusted code
fact by delegating to `await super().create_checkpoint(...)`. The legacy
`SQLiteSessionRepository` and `_records.py` remain untouched.

The anchored write callback performs these checks and inserts without releasing
its SQLite transaction:

```sql
SELECT generation, state, mutation_high_water
FROM workspace_rewind_coverage
WHERE workspace_fingerprint = ?;

SELECT 1
FROM workspace_mutations
WHERE workspace_fingerprint = ? AND status = 'prepared'
LIMIT 1;

INSERT INTO checkpoints(
  id, thread_id, label, metadata, created_at,
  message_sequence, event_sequence
) VALUES (?, ?, ?, ?, ?, ?, ?);

INSERT INTO checkpoint_rewind_facts(
  checkpoint_id, owner_thread_id, workspace_fingerprint,
  coverage_generation, mutation_sequence, coverage_state, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?);
```

The first row must exactly equal the supplied anchor; the second query must
return no row. A mismatch raises `SessionStorageError`, causing
`SessionDatabase.write` to roll back both inserts.

`CheckpointRecord.thread_id` remains the conversation/access-control scope.
`RewindCheckpointFact.owner_thread_id` is the code-effect scope. Root
checkpoints use the same ID for both. Child checkpoints retain their child
conversation thread while persisting the root owner supplied by their scoped
repository façade.

- [ ] **Step 4: Implement bounded observations and candidates**

Create `RewindObservationRepositoryMixin` with:

```python
async def get_rewind_checkpoint_anchor(
    self,
    coverage: CoverageToken,
    owner_thread_id: str,
) -> RewindCheckpointAnchor

async def observe_rewind(
    self,
    thread_id: str,
    checkpoint_id: str,
    limits: RewindReadLimits,
) -> RewindObservation

async def observe_rewind_heads(
    self,
    thread_id: str,
    workspace_fingerprint: str,
) -> RewindObservationHeads

async def list_rewind_candidates(
    self,
    thread_id: str,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> RewindCandidatePage
```

Finish the public composition in `rewind_repository.py`:

```python
from ._rewind_mutations import RewindMutationRepositoryMixin
from ._rewind_observations import RewindObservationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    RewindObservationRepositoryMixin,
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
```

Observation uses one `SessionDatabase.read` transaction. Select the checkpoint
with both ID and thread predicates; count messages with the thread predicate and
checkpoint/observed upper bounds; read coverage/mutation heads; fetch
`max_mutations + 1` and `max_paths + 1`. If either limit is crossed, return
`limit_exceeded=True` and an empty mutation tuple.

Use these exact query shapes inside that single read callback:

```sql
SELECT c.*, f.owner_thread_id AS rewind_owner_thread_id,
       f.workspace_fingerprint, f.coverage_generation,
       f.mutation_sequence, f.coverage_state,
       f.created_at AS rewind_created_at
FROM checkpoints AS c
LEFT JOIN checkpoint_rewind_facts AS f ON f.checkpoint_id = c.id
WHERE c.id = ? AND c.thread_id = ?;

SELECT COALESCE(MAX(sequence), 0)
FROM messages
WHERE thread_id = ?;

SELECT COUNT(*)
FROM messages
WHERE thread_id = ?
  AND sequence > ?
  AND sequence <= ?;

SELECT COALESCE(MAX(sequence), 0)
FROM events
WHERE thread_id = ?;

SELECT COALESCE(MAX(sequence), 0)
FROM workspace_mutations
WHERE workspace_fingerprint = ?;

SELECT *
FROM workspace_mutations
WHERE workspace_fingerprint = ?
  AND sequence > ?
  AND sequence <= ?
ORDER BY sequence
LIMIT ?;
```

The mutation limit parameter is `limits.max_mutations + 1`. Load all path rows
for the retained mutation sequences in one ordered query:

```python
marks = ",".join("?" for _ in mutation_sequences)
rows = connection.execute(
    "SELECT * FROM workspace_mutation_paths "
    f"WHERE mutation_sequence IN ({marks}) "
    "ORDER BY mutation_sequence, ordinal "
    "LIMIT ?",
    (*mutation_sequences, limits.max_paths + 1),
).fetchall()
```

Never interpolate user values; only the number of bound `?` parameters is
constructed from the already bounded mutation count. Fetch at most
`limits.max_paths + 1` rows and discard all mutation details when the extra row
exists.

Candidate pagination uses `(created_at DESC, id DESC)`. After decoding the
cursor, add:

```sql
AND (c.created_at < ? OR (c.created_at = ? AND c.id < ?))
ORDER BY c.created_at DESC, c.id DESC
LIMIT ?;
```

Fetch `limit + 1`, emit at most `limit`, and encode the last emitted
`created_at/id` as `next_cursor`. `has_message_bound` is
`c.message_sequence IS NOT NULL`; `has_code_anchor` is `f.checkpoint_id IS NOT
NULL`. Neither field claims current availability.

- [ ] **Step 5: Update Sessions Units and run the full Feature suite**

Record only the implemented public responsibilities in `AGENTS.md`, then run:

```powershell
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
& $python -m compileall -q src/code_agent/sessions
git diff --check
```

Expected: all Sessions tests pass.

- [ ] **Step 6: Commit the completed Sessions Feature**

```powershell
git add src/code_agent/sessions
git commit -m "关联回溯检查点：提供原子有界观测"
```

## Task 4: Expose deterministic Workspace rewind state

**Stage:** Workspace Feature implementation

**Files:**
- Create: `src/code_agent/workspace/rewind_state.py`
- Modify: `src/code_agent/workspace/errors.py`
- Modify: `src/code_agent/workspace/snapshot_store.py`
- Modify: `src/code_agent/workspace/_snapshot_artifacts.py`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Create: `src/code_agent/workspace/tests/test_rewind_state.py`
- Modify: `src/code_agent/workspace/tests/test_snapshot_store_integrity.py`

- [ ] **Step 1: Write failing deterministic-state tests**

```python
def test_prepare_edit_state_preserves_missing_preimage(self) -> None:
    plan = self.editor.plan_write("new.txt", "after\n")
    prepared = prepare_edit_state(self.editor, plan)
    self.assertFalse(prepared.before.existed)
    self.assertIsNone(prepared.before.sha256)
    self.assertEqual(
        prepared.after.sha256,
        hashlib.sha256(b"after\n").hexdigest(),
    )

def test_prepare_edit_state_rejects_a_stale_plan(self) -> None:
    plan = self.editor.plan_write("note.txt", "after\n")
    (self.root / "note.txt").write_bytes(b"user\n")
    with self.assertRaises(EditConflictError):
        prepare_edit_state(self.editor, plan)
```

Create these additional exact test methods:

- `test_dirty_binary_preimage_uses_raw_bytes_hash`
- `test_observe_states_sorts_canonical_paths`
- `test_relevant_digest_is_stable_for_equivalent_order`
- `test_relevant_digest_changes_with_existence_or_hash`
- `test_duplicate_observation_path_is_rejected`
- `test_observation_budget_exceeded_returns_no_partial_state`
- `test_observe_file_states_never_calls_editor_apply`

- [ ] **Step 2: Run state tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/workspace/tests -p 'test_rewind_state.py' -v
```

Expected: `code_agent.workspace.rewind_state` is missing.

- [ ] **Step 3: Implement Workspace state primitives**

Define:

```python
@dataclass(frozen=True)
class WorkspaceFileState:
    relative_path: str
    existed: bool
    sha256: str | None
    size: int

@dataclass(frozen=True)
class PreparedEditState:
    snapshot: WorkspaceSnapshot
    before: WorkspaceFileState
    after: WorkspaceFileState

def prepare_edit_state(
    editor: WorkspaceEditor,
    plan: EditPlan,
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> PreparedEditState

def observe_file_states(
    editor: WorkspaceEditor,
    paths: tuple[str, ...],
    *,
    max_total_bytes: int = DEFAULT_SNAPSHOT_BYTES,
) -> tuple[WorkspaceFileState, ...]

def relevant_path_digest(states: tuple[WorkspaceFileState, ...]) -> str
```

`prepare_edit_state` snapshots the plan path, verifies existence/hash against
the plan, and computes the postimage from `plan.after_text.encode("utf-8")`.
`relevant_path_digest` hashes canonical JSON sorted by canonical path.
All required `EditPlan` fields are already public, so `edits.py` remains
untouched at 270 lines.

- [ ] **Step 4: Write failing missing/corrupt snapshot tests**

Add exact assertions:

```python
def test_missing_manifest_has_a_stable_error(self) -> None:
    handle = self.store.save(self.editor.snapshot(("note.txt",)))
    (self.store.root / "manifests" / f"{handle.identifier}.json").unlink()
    with self.assertRaises(SnapshotMissingError):
        self.store.load(handle)

def test_tampered_manifest_has_a_stable_error(self) -> None:
    handle = self.store.save(self.editor.snapshot(("note.txt",)))
    manifest = self.store.root / "manifests" / f"{handle.identifier}.json"
    manifest.write_bytes(manifest.read_bytes() + b"x")
    with self.assertRaises(SnapshotIntegrityError):
        self.store.load(handle)
```

- [ ] **Step 5: Run snapshot error tests to verify RED**

```powershell
& $python -m unittest src.code_agent.workspace.tests.test_snapshot_store_integrity -v
```

Expected: the dedicated exception imports or assertions fail.

- [ ] **Step 6: Implement missing/corrupt snapshot classification**

```python
class SnapshotMissingError(WorkspaceError):
    """A referenced snapshot manifest or blob does not exist."""

class SnapshotIntegrityError(WorkspaceError):
    """A snapshot artifact exists but fails integrity validation."""
```

Map absent manifest/blob to `SnapshotMissingError`; map handle, manifest, blob,
workspace fingerprint, path, size, and digest mismatch to
`SnapshotIntegrityError`. Expose a read-only
`WorkspaceSnapshotStore.workspace_fingerprint` property.

- [ ] **Step 7: Run the full Workspace suite and commit**

```powershell
& $python -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
& $python -m compileall -q src/code_agent/workspace
git diff --check
git add src/code_agent/workspace
git commit -m "补充工作区回溯状态：暴露确定性前后镜像"
```

## Task 5: Propagate explicit Core action lineage

**Stage:** Core Feature implementation

**Files:**
- Create: `src/code_agent/core/action_execution.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/engine_actions.py`
- Modify: `src/code_agent/core/AGENTS.md`
- Create: `src/code_agent/core/tests/test_action_execution.py`
- Modify: `src/code_agent/core/tests/_engine_support.py`
- Modify: `src/code_agent/core/tests/test_engine_tools.py`

- [ ] **Step 1: Write failing context validation tests**

```python
def test_action_context_keeps_owner_and_origin_distinct(self) -> None:
    value = ActionExecutionContext(
        owner_thread_id="parent",
        origin_thread_id="child",
        request_id="request",
        task_id="task",
        parent_request_id="delegate",
    )
    self.assertEqual(value.owner_thread_id, "parent")
    self.assertEqual(value.origin_thread_id, "child")

def test_action_lineage_rejects_blank_owner(self) -> None:
    with self.assertRaises(ValueError):
        ActionLineage(" ")
```

- [ ] **Step 2: Run context tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/core/tests -p 'test_action_execution.py' -v
```

Expected: module import failure.

- [ ] **Step 3: Implement immutable context types**

```python
@dataclass(frozen=True)
class ActionLineage:
    owner_thread_id: str
    task_id: str | None = None
    parent_request_id: str | None = None

@dataclass(frozen=True)
class ActionExecutionContext:
    owner_thread_id: str
    origin_thread_id: str
    request_id: str
    task_id: str | None = None
    parent_request_id: str | None = None
```

Validate every present identifier as bounded non-blank text.

- [ ] **Step 4: Write failing engine propagation tests**

Use a recording dispatcher and assert:

```python
self.assertEqual(
    dispatcher.context,
    ActionExecutionContext(
        owner_thread_id=thread_id,
        origin_thread_id=thread_id,
        request_id="call-1",
        task_id=task.id,
        parent_request_id=None,
    ),
)
```

Add a second test that constructs `AgentEngine` with the keyword
`action_lineage=ActionLineage("parent", "parent-task", "delegate")`; assert
owner `parent`, origin child thread, task `parent-task`, and parent request
`delegate`.

- [ ] **Step 5: Run engine propagation tests to verify RED**

```powershell
& $python -m unittest src.code_agent.core.tests.test_engine_tools -v
```

Expected: dispatcher receives no `execution_context` keyword or the expected
recording field is unset.

- [ ] **Step 6: Pass context explicitly to dispatchers**

Add the keyword-only protocol argument:

```python
async def dispatch(
    self,
    request: ActionRequest,
    cancellation: CancellationToken,
    task_authorization: TaskAuthorization | None = None,
    *,
    execution_context: ActionExecutionContext | None = None,
) -> ActionResult:
```

Add `action_lineage: ActionLineage | None = None` to `AgentEngine.__init__`.
After `ACTION_STARTED` persistence, construct the context and pass it to
`self._actions.dispatch`. Unavailable/denied-before-dispatch tools do not
produce a context or mutation.

For a root engine, use the current task ID. For a child engine, preserve the
lineage task ID even though the advisory child thread has no independent
`TaskRecord`.

Update Core test fakes to accept and record the keyword without changing their
existing behavior.

- [ ] **Step 7: Verify Core GREEN, update Units, and commit**

```powershell
& $python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
& $python -m compileall -q src/code_agent/core
git diff --check
git add src/code_agent/core
git commit -m "传递动作归因：显式区分父子线程执行上下文"
```

## Task 6: Build pure preview models and read-only TUI commands

**Stage:** Interfaces Feature implementation

**Files:**
- Create: `src/code_agent/interfaces/rewind_models.py`
- Create: `src/code_agent/interfaces/rewind_view.py`
- Create: `src/code_agent/interfaces/tui_rewind_commands.py`
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/command_availability.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Create: `src/code_agent/interfaces/tests/test_rewind_view.py`
- Create: `src/code_agent/interfaces/tests/test_tui_rewind_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`

- [ ] **Step 1: Write failing pure-preview tests**

```python
def test_conversation_preview_ignores_code_failure(self) -> None:
    facts = _facts(
        message_count=2,
        code_reason=RewindDisabledReason.SNAPSHOT_INVALID,
    )
    preview = build_rewind_preview(RewindKind.CONVERSATION, facts)
    self.assertTrue(preview.enabled)
    self.assertEqual(preview.disabled_reasons, ())

def test_both_orders_reasons_stably(self) -> None:
    facts = _facts(
        conversation_reason=RewindDisabledReason.MESSAGE_BOUND_MISSING,
        code_reason=RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE,
    )
    preview = build_rewind_preview(RewindKind.BOTH, facts)
    self.assertEqual(
        preview.disabled_reasons,
        (
            RewindDisabledReason.MESSAGE_BOUND_MISSING,
            RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE,
        ),
    )
    self.assertFalse(preview.apply_available)
    self.assertFalse(preview.requires_git_reset)
```

Create these additional exact test methods:

- `test_code_preview_does_not_require_message_bound`
- `test_zero_message_and_zero_path_previews_are_enabled`
- `test_preview_models_are_frozen`
- `test_duplicate_rewind_paths_are_rejected`
- `test_invalid_digest_count_and_time_are_rejected`
- `test_renderer_strips_ansi_and_control_characters`
- `test_renderer_reports_hidden_path_count`
- `test_confirmation_flag_equals_enabled_state`

- [ ] **Step 2: Run preview tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_rewind_view.py' -v
```

Expected: rewind modules are missing.

- [ ] **Step 3: Implement pure models and projection**

Define stable enums exactly as approved:

```python
class RewindKind(str, Enum):
    CONVERSATION = "conversation"
    CODE = "code"
    BOTH = "both"

class RewindDisabledReason(str, Enum):
    CHECKPOINT_NOT_FOUND = "checkpoint-not-found"
    MESSAGE_BOUND_MISSING = "message-bound-missing"
    MESSAGE_BOUND_INVALID = "message-bound-invalid"
    CODE_COVERAGE_UNAVAILABLE = "code-coverage-unavailable"
    CODE_JOURNAL_INCOMPLETE = "code-journal-incomplete"
    PENDING_WORKSPACE_MUTATION = "pending-workspace-mutation"
    SNAPSHOT_MISSING = "snapshot-missing"
    SNAPSHOT_INVALID = "snapshot-invalid"
    WORKSPACE_CONFLICT = "workspace-conflict"
    PREVIEW_LIMIT_EXCEEDED = "preview-limit-exceeded"
    SOURCE_CHANGED_DURING_PREVIEW = "source-changed-during-preview"
```

Add the frozen records and protocol below. Implement `build_rewind_preview`,
`render_rewind_preview`, and `render_rewind_candidates`. Render fixed
`preview only`, `apply unavailable`, and `no git reset` statements and sanitize
all untrusted labels/paths with the existing terminal safe-text path.

Use these exact field names:

```python
@dataclass(frozen=True)
class RewindAsOf:
    message_sequence: int | None
    event_sequence: int | None
    mutation_sequence: int | None
    coverage_generation: int | None
    relevant_path_digest: str | None
    captured_at: datetime

@dataclass(frozen=True)
class RewindPath:
    path: str
    baseline_provenance: str
    preserves_pre_agent_baseline: bool

@dataclass(frozen=True)
class RewindFacts:
    checkpoint_id: str
    checkpoint_label: str
    checkpoint_created_at: datetime
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    conversation_disabled_reason: RewindDisabledReason | None
    code_disabled_reason: RewindDisabledReason | None

@dataclass(frozen=True)
class RewindPreview:
    kind: RewindKind
    checkpoint_id: str
    checkpoint_label: str
    as_of: RewindAsOf
    conversation_messages: int
    code_paths: tuple[RewindPath, ...]
    disabled_reasons: tuple[RewindDisabledReason, ...]
    enabled: bool
    requires_confirmation: bool
    apply_available: bool
    requires_git_reset: bool

@dataclass(frozen=True)
class RewindCheckpointCandidate:
    checkpoint_id: str
    label: str
    created_at: datetime
    has_message_bound: bool
    has_code_anchor: bool

@dataclass(frozen=True)
class RewindCheckpointPage:
    items: tuple[RewindCheckpointCandidate, ...]
    next_cursor: str | None

class RewindPreviewSource(Protocol):
    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage:
        """Return one bounded page of checkpoint candidates."""

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview:
        """Build a read-only preview without mutating any source."""
```

Missing message/event bounds and missing code anchors remain `None`; never
coerce them to zero. Validate the three optional sequence fields as
non-negative when present. The candidate booleans are facets only and are never
used as final availability decisions.

- [ ] **Step 4: Verify and commit the pure preview layer**

```powershell
& $python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_rewind_view.py' -v
git diff --check
git add src/code_agent/interfaces/rewind_models.py src/code_agent/interfaces/rewind_view.py src/code_agent/interfaces/tests/test_rewind_view.py
git commit -m "建立回溯投影：定义纯预览模型"
```

- [ ] **Step 5: Write failing command tests**

```python
async def test_preview_requires_explicit_checkpoint_and_kind(self) -> None:
    source = FakeRewindSource()
    result = await handle_rewind_command(
        source, "thread-1", "preview checkpoint-1 both"
    )
    self.assertTrue(result.handled)
    self.assertEqual(
        source.preview_calls,
        [("thread-1", "checkpoint-1", RewindKind.BOTH)],
    )

def test_rewind_is_hidden_without_service(self) -> None:
    self.assertEqual(
        REGISTRY.parse("/rewind list", set())[2],
        "unknown or unavailable slash command",
    )
```

Create these additional exact test methods:

- `test_chinese_and_english_list_actions_match`
- `test_list_cursor_is_forwarded_unchanged`
- `test_command_without_current_thread_returns_visible_error`
- `test_invalid_action_kind_and_arity_return_stable_errors`
- `test_list_output_says_candidate_only`
- `test_handler_never_calls_controller_provider_dispatcher_or_approval`

- [ ] **Step 6: Run command tests to verify RED**

```powershell
& $python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_tui_rewind_commands.py' -v
& $python -m unittest src.code_agent.interfaces.tests.test_tui_commands -v
```

Expected: missing handler/module and unavailable command assertions fail.

- [ ] **Step 7: Register and delegate `/rewind`**

Add a `CommandSpec` named `回溯`, alias `rewind`, requirement `rewind`, with
`列表/list` and `预览/preview` actions. Add `TuiCommandKind.REWIND`, preserve
the raw instruction, and make
`handle_rewind_command(source, thread_id, instruction)` return a frozen
`RewindCommandResult(handled, display_kind, text)`. Extend
`available_services` to report `rewind` when a host has a non-null `rewind`
attribute. Do not modify the 297-line `windows_tui.py`; the integration subclass
in Task 8 owns the optional source and appends the returned text.

- [ ] **Step 8: Verify Interfaces GREEN, update Units, and commit**

```powershell
& $python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
& $python -m compileall -q src/code_agent/interfaces
git diff --check
git add src/code_agent/interfaces
git commit -m "新增回溯界面：提供纯预览与只读命令"
```

## Task 7: Integrate the gate, capture coordinator, and checkpoint ordering

**Stage:** Integration

**Files:**
- Create: `code_agent_win/rewind_gate.py`
- Create: `code_agent_win/rewind_capture.py`
- Create: `code_agent_win/rewind_sessions.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/AGENTS.md`
- Create: `tests/test_rewind_gate.py`
- Create: `tests/test_rewind_capture.py`
- Create: `tests/test_rewind_checkpoint_ordering.py`
- Create: `tests/test_rewind_lineage_integration.py`
- Create: `tests/test_rewind_plugin_integration.py`
- Modify: `tests/test_app_dispatcher.py`

- [ ] **Step 1: Write failing cross-process gate tests**

Create `test_two_gate_instances_are_mutually_exclusive`,
`test_subprocess_holder_causes_bounded_timeout`,
`test_release_allows_next_holder`, `test_crashed_holder_is_released_by_sqlite`,
`test_different_fingerprints_do_not_block`, and
`test_release_may_run_on_a_different_worker_thread`. The two in-process tests
contain these assertions:

```python
async def test_two_gate_instances_are_mutually_exclusive(self) -> None:
    first = WorkspaceMutationGate(self.state_root, "a" * 64)
    second = WorkspaceMutationGate(self.state_root, "a" * 64)
    lease = await first.acquire(timeout_s=1.0)
    try:
        with self.assertRaises(WorkspaceGateTimeout):
            await second.acquire(timeout_s=0.05)
    finally:
        await lease.release()
    released = await second.acquire(timeout_s=1.0)
    await released.release()

async def test_release_may_run_on_a_different_worker_thread(self) -> None:
    executors = (
        ThreadPoolExecutor(max_workers=1),
        ThreadPoolExecutor(max_workers=1),
    )
    calls = itertools.count()

    async def alternating_to_thread(function, *args):
        loop = asyncio.get_running_loop()
        executor = executors[next(calls) % 2]
        bound = functools.partial(function, *args)
        return await loop.run_in_executor(executor, bound)

    try:
        with patch(
            "code_agent_win.rewind_gate.asyncio.to_thread",
            side_effect=alternating_to_thread,
        ):
            gate = WorkspaceMutationGate(self.state_root, "a" * 64)
            lease = await gate.acquire(timeout_s=1.0)
            await lease.release()
            reacquired = await gate.acquire(timeout_s=1.0)
            await reacquired.release()
    finally:
        for executor in executors:
            executor.shutdown(wait=True)
```

- [ ] **Step 2: Run gate tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_gate -v
```

Expected: import failure for `code_agent_win.rewind_gate`.

- [ ] **Step 3: Implement the independent SQLite gate**

Store the lock database at:

```text
<product-state>/rewind/<workspace-fingerprint>/mutation-gate.sqlite3
```

`WorkspaceMutationGate.acquire()` first acquires one in-process `asyncio.Lock`,
then uses `asyncio.to_thread` to open an independent connection with:

```python
connection = sqlite3.connect(
    gate_path,
    timeout=timeout_s,
    isolation_level=None,
    check_same_thread=False,
)
connection.execute(f"PRAGMA busy_timeout = {max(1, int(timeout_s * 1000))}")
connection.execute("BEGIN IMMEDIATE")
```

`check_same_thread=False` is permitted only because the connection is owned by
one lease, never exposed, and every operation is serialized: after acquire,
the only permitted connection operation is one release. A per-lease
`asyncio.Lock` and `_released` flag make concurrent/double release harmless.
`WorkspaceGateLease.release()` runs `ROLLBACK` and `close` together in one
`asyncio.to_thread` callback, then releases the gate's in-process lock exactly
once even when rollback reports an error.

Do not reuse the Sessions database: its write lock would block durable
mutation/checkpoint commits while the gate is held.

- [ ] **Step 4: Verify and commit the gate**

```powershell
& $python -m unittest tests.test_rewind_gate -v
git diff --check
git add code_agent_win/rewind_gate.py tests/test_rewind_gate.py
git commit -m "新增工作区门闩：跨进程序列化回溯写入"
```

- [ ] **Step 5: Write failing known-edit and gap tests**

Cover this ordering:

```python
self.assertEqual(
    calls,
    [
        "gate.acquire",
        "workspace.snapshot",
        "snapshot.save",
        "sessions.prepare",
        "editor.apply",
        "workspace.observe",
        "sessions.complete",
        "gate.release",
    ],
)
```

Create these additional exact test methods:

- `test_snapshot_or_prepare_failure_causes_zero_file_write`
- `test_apply_failure_aborts_only_when_current_state_is_preimage`
- `test_completion_failure_leaves_prepared_and_returns_error`
- `test_cancellation_cannot_skip_reconciliation_or_gate_release`
- `test_cache_invalidates_only_after_completed_journal`
- `test_plugin_alias_uses_translated_typed_target`
- `test_command_verification_and_write_mcp_record_gap_before_call`
- `test_invalidated_workspace_continues_typed_edit_as_gap`
- `test_delegate_parent_does_not_record_duplicate_mutation`

- [ ] **Step 6: Run capture tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_capture tests.test_app_dispatcher -v
```

Expected: missing coordinator and dispatcher does not call the capture ledger.

- [ ] **Step 7: Implement `RewindCaptureCoordinator`**

Expose:

```python
class RewindCaptureCoordinator:
    async def apply_edit(
        self,
        context: ActionExecutionContext,
        request: ActionRequest,
        plan: EditPlan,
    ) -> None

    async def record_gap(
        self,
        context: ActionExecutionContext,
        request: ActionRequest,
        reason: str,
    ) -> None
```

`apply_edit` acquires the gate; prepares deterministic state; saves the
snapshot; durably prepares the journal; applies the edit through
`asyncio.to_thread`; verifies the postimage; completes the journal; and releases
the gate in `finally`. A successful filesystem write followed by failed
completion remains `prepared` and the dispatcher returns an error.
Both `apply_edit` and `record_gap` copy
`ActionExecutionContext.parent_request_id` into their Sessions request record.

If Sessions reports that coverage was already invalidated by an earlier unknown
writer, the coordinator records a durable
`coverage-already-invalidated` gap under the gate, executes the approved typed
edit, and keeps every later code/both preview disabled. Invalidated coverage
must not disable ordinary editing and must never be interpreted as reenabled.

After policy/approval and translated-target validation, dispatcher behavior is:

- workspace-relative `write_file`/`replace_text`: call `apply_edit`;
- known absolute FULL_LOCAL path outside the workspace: execute as explicitly
  out of workspace rewind scope and do not put an absolute path in a snapshot;
- `run_command`, `run_verification`, write/critical MCP, and untyped
  write/critical plugin: call `record_gap` immediately before execution;
- read-only tools: no journal row;
- `delegate_agent`: no parent row because child actions record their own facts.

Require a non-`None` execution context for every write/gap path.

- [ ] **Step 8: Verify and commit capture behavior**

```powershell
& $python -m unittest tests.test_rewind_capture tests.test_app_dispatcher -v
git diff --check
git add code_agent_win/rewind_capture.py code_agent_win/action_dispatcher.py tests/test_rewind_capture.py tests/test_app_dispatcher.py
git commit -m "捕获工作区变更：持久化已知写入与覆盖缺口"
```

- [ ] **Step 9: Write failing checkpoint-order tests**

Use fakes/barriers to prove only these orders:

```text
checkpoint commit -> mutation prepare -> file write -> mutation complete
mutation prepare -> file write -> mutation complete -> checkpoint commit
```

Assert no checkpoint can commit a pre-mutation high-water after the file is
already changed.

Also add the owner-scope assertion:

```python
async def test_child_facade_anchors_checkpoint_to_root_owner(self) -> None:
    root_thread = await self.base.create_thread()
    child_thread = await self.base.create_thread()
    child_repository = self.coordinated.for_owner(root_thread)
    checkpoint_id = await child_repository.create_checkpoint(
        child_thread,
        "child checkpoint",
    )
    observation = await self.base.observe_rewind(
        child_thread,
        checkpoint_id,
        RewindReadLimits(100, 100),
    )
    self.assertEqual(observation.checkpoint.thread_id, child_thread)
    self.assertIsNotNone(observation.checkpoint_fact)
    assert observation.checkpoint_fact is not None
    self.assertEqual(
        observation.checkpoint_fact.owner_thread_id,
        root_thread,
    )
```

- [ ] **Step 10: Run checkpoint-order tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_checkpoint_ordering -v
```

Expected: coordinated wrapper is missing or the barrier permits an invalid
interleaving.

- [ ] **Step 11: Implement `CoordinatedSessionRepository`**

Implement the complete scoped façade:

```python
class CoordinatedSessionRepository:
    def __init__(
        self,
        base: RewindSessionRepository,
        gate: WorkspaceMutationGate,
        coverage_token: CoverageToken,
        *,
        fixed_owner_thread_id: str | None = None,
    ) -> None:
        self._base = base
        self._gate = gate
        self._coverage_token = coverage_token
        self._fixed_owner_thread_id = fixed_owner_thread_id

    def __getattr__(self, name: str) -> object:
        return getattr(self._base, name)

    def for_owner(
        self,
        owner_thread_id: str,
    ) -> "CoordinatedSessionRepository":
        if not isinstance(owner_thread_id, str) or not owner_thread_id.strip():
            raise ValueError("owner_thread_id must be non-blank")
        return CoordinatedSessionRepository(
            self._base,
            self._gate,
            self._coverage_token,
            fixed_owner_thread_id=owner_thread_id,
        )

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> str:
        code_owner = self._fixed_owner_thread_id or thread_id
        lease = await self._gate.acquire(timeout_s=5.0)
        try:
            anchor = await self._base.get_rewind_checkpoint_anchor(
                self._coverage_token,
                code_owner,
            )
            return await self._base.create_checkpoint(
                thread_id,
                label,
                metadata,
                rewind_anchor=anchor,
            )
        finally:
            await lease.release()
```

Gate or anchor failure propagates; it never falls back to an unanchored
code-capable checkpoint.

Inject the wrapper into AgentEngine, foreground task controller, and semantic
context compactor. Child engines and their compactors receive
`coordinated.for_owner(parent_context.owner_thread_id)`. Keep the base
repository available to capture/runtime data methods. Conversation queries
still use the checkpoint's child/root thread; code queries use the persisted
fixed owner.

- [ ] **Step 12: Write failing child-lineage integration tests**

Add tests asserting:

```python
self.assertEqual(child_effect.owner_thread_id, parent_thread_id)
self.assertNotEqual(child_effect.origin_thread_id, parent_thread_id)
self.assertEqual(child_effect.task_id, parent_task_id)
self.assertEqual(child_effect.parent_request_id, delegate_request_id)
self.assertEqual(parent_delegate_mutation_count, 0)
```

Use two concurrent child requests and assert each effect retains its own parent
request ID. A writable child with no lineage must return a structured error
before its write dispatcher runs.

- [ ] **Step 13: Run child-lineage tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_lineage_integration -v
```

Expected: context is dropped by `RestrictedDispatcher` or child engines retain
their own thread as owner.

- [ ] **Step 14: Preserve lineage through child dispatch**

Update `RestrictedDispatcher` to forward `execution_context`. Pass parent
lineage into `SubagentRuntime`/`EngineChildRunner`, construct child engines with:

```python
ActionLineage(
    owner_thread_id=parent_context.owner_thread_id,
    task_id=parent_context.task_id,
    parent_request_id=parent_context.request_id,
)
```

Typed child writes use the inherited owner/task; child gaps invalidate the same
workspace coverage; read-only children may run without creating mutation facts.

- [ ] **Step 15: Run integration capture tests and commit**

```powershell
& $python -m unittest tests.test_rewind_gate tests.test_rewind_capture tests.test_rewind_checkpoint_ordering tests.test_rewind_lineage_integration tests.test_app_dispatcher -v
& $python -m compileall -q code_agent_win tests
git diff --check
git add code_agent_win tests/test_rewind_gate.py tests/test_rewind_capture.py tests/test_rewind_checkpoint_ordering.py tests/test_rewind_lineage_integration.py tests/test_app_dispatcher.py
git commit -m "接入回溯捕获：串行化变更与检查点"
```

## Task 8: Validate observations and wire the read-only runtime

**Stage:** Integration

**Files:**
- Create: `code_agent_win/rewind_runtime.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/AGENTS.md`
- Create: `tests/test_rewind_runtime.py`
- Create: `tests/test_rewind_tui_integration.py`

- [ ] **Step 1: Write failing runtime tests**

Cover:

```python
async def test_v10_checkpoint_can_preview_conversation_only(self) -> None:
    preview = await self.runtime.preview(
        self.thread_id, self.legacy_checkpoint, RewindKind.CONVERSATION
    )
    self.assertTrue(preview.enabled)
    self.assertEqual(preview.conversation_messages, 2)

async def test_current_tip_change_disables_code(self) -> None:
    (self.root / "note.txt").write_bytes(b"user changed\n")
    preview = await self.runtime.preview(
        self.thread_id, self.checkpoint, RewindKind.CODE
    )
    self.assertEqual(
        preview.disabled_reasons,
        (RewindDisabledReason.WORKSPACE_CONFLICT,),
    )

def test_child_checkpoint_selects_root_owned_effects(self) -> None:
    child_effect = SimpleNamespace(
        sequence=3,
        owner_thread_id="thread-root",
        origin_thread_id="thread-child",
        status=RewindMutationStatus.COMPLETED,
        paths=(),
    )
    foreign_effect = SimpleNamespace(
        sequence=4,
        owner_thread_id="thread-other",
        origin_thread_id="thread-other",
        status=RewindMutationStatus.COMPLETED,
        paths=(),
    )
    observation = SimpleNamespace(
        checkpoint_fact=SimpleNamespace(owner_thread_id="thread-root"),
        mutations=(foreign_effect, child_effect),
    )
    selected = _owned_completed_mutations(observation)
    self.assertEqual(tuple(item.sequence for item in selected), (3,))
    self.assertEqual(selected[0].origin_thread_id, "thread-child")

def test_path_projection_uses_earliest_owned_baseline(self) -> None:
    earliest = SimpleNamespace(
        sequence=6,
        paths=(
            SimpleNamespace(
                path="note.txt",
                baseline=RewindBaseline.GIT_UNTRACKED,
            ),
            SimpleNamespace(
                path="created.txt",
                baseline=RewindBaseline.ABSENT,
            ),
        ),
    )
    later = SimpleNamespace(
        sequence=8,
        paths=(
            SimpleNamespace(
                path="note.txt",
                baseline=RewindBaseline.GIT_UNSTAGED,
            ),
        ),
    )
    self.assertEqual(
        _project_rewind_paths((later, earliest)),
        (
            RewindPath("created.txt", "absent", True),
            RewindPath("note.txt", "git-untracked", True),
        ),
    )

def test_conversation_count_and_bound_reasons_map_exactly(self) -> None:
    def observation(
        bound: int | None,
        head: int,
        count: int | None,
    ) -> object:
        return SimpleNamespace(
            checkpoint=SimpleNamespace(message_sequence=bound),
            heads=SimpleNamespace(message_sequence=head),
            conversation_message_count=count,
        )

    self.assertEqual(_conversation_projection(observation(5, 9, 3)), (3, None))
    self.assertEqual(
        _conversation_projection(observation(None, 9, None)),
        (0, RewindDisabledReason.MESSAGE_BOUND_MISSING),
    )
    self.assertEqual(
        _conversation_projection(observation(10, 9, None)),
        (0, RewindDisabledReason.MESSAGE_BOUND_INVALID),
    )
    self.assertEqual(
        _conversation_projection(observation(5, 9, None)),
        (0, RewindDisabledReason.MESSAGE_BOUND_INVALID),
    )
```

Create these additional exact test methods:

- `test_middle_and_old_checkpoint_reverse_same_path_chain`
- `test_missing_and_invalid_artifacts_map_to_distinct_reasons`
- `test_pending_and_gap_map_to_stable_reasons`
- `test_disjoint_foreign_effect_is_preserved`
- `test_overlapping_foreign_effect_is_conflict`
- `test_limit_overflow_returns_no_partial_paths`
- `test_head_or_path_move_retries_once_then_disables`
- `test_cross_thread_checkpoint_maps_to_not_found`
- `test_zero_mutation_code_preview_is_enabled`

- [ ] **Step 2: Run runtime tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_runtime -v
```

Expected: import failure for `code_agent_win.rewind_runtime`.

- [ ] **Step 3: Implement `RewindRuntime`**

Implement the `RewindPreviewSource` protocol:

```python
class RewindRuntime:
    async def list_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCheckpointPage

    async def preview(
        self,
        thread_id: str,
        checkpoint_id: str,
        kind: RewindKind,
    ) -> RewindPreview
```

For code facts:

1. call Sessions `observe_rewind`;
2. map legacy/missing coverage without treating it as corruption;
3. decode every handle with `SnapshotHandle.from_dict`;
4. load artifacts through `asyncio.to_thread`;
5. validate paths, preimage hashes, same-path reverse continuity, owner/foreign
   overlap, and current tip;
6. compute the bounded relevant-path digest;
7. reread Sessions heads and paths;
8. retry the whole observation once if either moved;
9. return `source-changed-during-preview` on a second move.

Map missing artifact, corrupt/foreign artifact, conflict, pending, gap, and
limit states to their exact Interface enums. Never expose an apply method.
Construct `RewindReadLimits` with the Sessions defaults above, keep snapshot
loads under the existing 10 MB per-snapshot limit, and render at most 20 paths
or candidates while reporting the hidden count.

Map the nullable Sessions count into the non-null Interfaces field only through
this function:

```python
def _conversation_projection(
    observation: RewindObservation,
) -> tuple[int, RewindDisabledReason | None]:
    bound = observation.checkpoint.message_sequence
    if bound is None:
        return 0, RewindDisabledReason.MESSAGE_BOUND_MISSING
    count = observation.conversation_message_count
    if count is None or bound > observation.heads.message_sequence:
        return 0, RewindDisabledReason.MESSAGE_BOUND_INVALID
    return count, None
```

The disabled-state zero is never rendered as proof of zero messages; it is
always paired with its missing/invalid reason. Event bounds do not affect a
conversation-only preview.

Use this stable code-reason selection before artifact I/O:

```python
if observation.limit_exceeded:
    code_reason = RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
elif observation.checkpoint_fact is None:
    code_reason = RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE
elif observation.checkpoint_fact.coverage_state is RewindCoverageState.INVALIDATED:
    code_reason = RewindDisabledReason.CODE_JOURNAL_INCOMPLETE
elif any(
    item.status is RewindMutationStatus.PREPARED
    for item in observation.mutations
):
    code_reason = RewindDisabledReason.PENDING_WORKSPACE_MUTATION
elif any(
    item.status is RewindMutationStatus.GAP
    for item in observation.mutations
):
    code_reason = RewindDisabledReason.CODE_JOURNAL_INCOMPLETE
else:
    code_reason = None
```

Then validate the chain with the exact ordering below:

```python
code_owner_thread_id = observation.checkpoint_fact.owner_thread_id
owned = _owned_completed_mutations(observation)
owned_paths = {
    path.path
    for mutation in owned
    for path in mutation.paths
}
foreign_overlap = any(
    mutation.owner_thread_id != code_owner_thread_id
    and mutation.status is RewindMutationStatus.COMPLETED
    and any(path.path in owned_paths for path in mutation.paths)
    for mutation in observation.mutations
)
if foreign_overlap:
    code_reason = RewindDisabledReason.WORKSPACE_CONFLICT
```

Owner and path projection use the persisted code scope and the earliest owned
effect, never the preview's conversation thread:

```python
def _owned_completed_mutations(
    observation: RewindObservation,
) -> tuple[RewindMutationRecord, ...]:
    checkpoint_fact = observation.checkpoint_fact
    if checkpoint_fact is None:
        return ()
    return tuple(
        mutation
        for mutation in sorted(
            observation.mutations,
            key=lambda item: item.sequence,
        )
        if mutation.owner_thread_id == checkpoint_fact.owner_thread_id
        and mutation.status is RewindMutationStatus.COMPLETED
    )

def _project_rewind_paths(
    mutations: tuple[RewindMutationRecord, ...],
) -> tuple[RewindPath, ...]:
    earliest_by_path: dict[str, RewindMutationPath] = {}
    for mutation in sorted(mutations, key=lambda item: item.sequence):
        for path_fact in mutation.paths:
            earliest_by_path.setdefault(path_fact.path, path_fact)
    return tuple(
        RewindPath(
            path=path,
            baseline_provenance=earliest_by_path[path].baseline.value,
            preserves_pre_agent_baseline=True,
        )
        for path in sorted(earliest_by_path)
    )
```

Call `_project_rewind_paths` only after every snapshot, continuity, foreign
overlap, and current-tip validation succeeds. Disabled code previews always
return an empty path tuple. Here `preserves_pre_agent_baseline=True` means the
exact preimage before the earliest effect attributed to that root owner was
verified; `baseline_provenance` remains a non-authoritative display
classification and may be `unknown`.

For each owned mutation in ascending sequence, load exactly one inverse
snapshot and require its path/existence/SHA-256 tuple to equal the persisted
preimage tuple. Group path facts by path. For every adjacent owned fact on one
path, require `earlier.after_existed/after_sha256` to equal
`later.before_existed/before_sha256`. Finally observe current path states and
require each to equal the latest owned postimage. Any mismatch maps to
`workspace-conflict`; `SnapshotMissingError` maps to `snapshot-missing`;
`SnapshotIntegrityError`, strict handle decode failure, or snapshot/path fact
mismatch maps to `snapshot-invalid`.

The retry loop is exactly two complete attempts:

```python
for attempt in range(2):
    observation = await sessions.observe_rewind(thread_id, checkpoint_id, limits)
    facts, first_states = await _validate_observation(observation)
    heads = await sessions.observe_rewind_heads(thread_id, workspace_fingerprint)
    second_states = await asyncio.to_thread(
        observe_file_states, editor, tuple(path.path for path in facts.code_paths)
    )
    if heads == observation.heads and second_states == first_states:
        return build_rewind_preview(kind, facts)
return _disabled_preview(
    kind,
    checkpoint_id,
    RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
)
```

`_validate_observation` and `_disabled_preview` are private functions in
`rewind_runtime.py`; tests call only the public runtime. When checkpoint lookup
raises `SessionNotFound`, return `checkpoint-not-found` without revealing
whether the ID belongs to another thread.

- [ ] **Step 4: Verify and commit the runtime**

```powershell
& $python -m unittest tests.test_rewind_runtime -v
git diff --check
git add code_agent_win/rewind_runtime.py tests/test_rewind_runtime.py
git commit -m "验证回溯观测：生成可信只读预览"
```

- [ ] **Step 5: Write failing TUI/application wiring tests**

Create these exact methods in `tests/test_rewind_tui_integration.py`:

- `test_application_injects_rewind_and_command_is_visible`
- `test_list_is_paginated_and_says_candidate_only`
- `test_conversation_code_and_both_previews_render`
- `test_disabled_reason_value_is_stable`
- `test_footer_says_preview_only_apply_unavailable_and_no_git_reset`
- `test_command_calls_no_provider_action_approval_or_checkpoint`

- [ ] **Step 6: Run TUI/application tests to verify RED**

```powershell
& $python -m unittest tests.test_rewind_tui_integration tests.test_agent_app -v
```

Expected: application has no rewind runtime and `/rewind` remains unavailable.

- [ ] **Step 7: Wire one store/coordinator/runtime into the application**

Use product state:

```text
%LOCALAPPDATA%\chaos-agent\rewind-snapshots
%LOCALAPPDATA%\chaos-agent\rewind\<fingerprint>\mutation-gate.sqlite3
```

Construct one `WorkspaceSnapshotStore`, gate, capture coordinator, coordinated
Sessions wrapper, and `RewindRuntime`. Share them across main and child engines.
Inject `rewind=runtime` into the TUI and expose
`Application.rewind: RewindRuntime | None`. Convert `Application` construction
to keyword arguments to prevent positional-field drift.

- [ ] **Step 8: Verify root integration and commit**

```powershell
& $python -m unittest discover -s tests -p 'test_rewind_*.py' -v
& $python -m unittest tests.test_agent_app tests.test_command_integration tests.test_subagent_integration -v
& $python -m compileall -q code_agent_win tests
git diff --check
git add code_agent_win tests/test_rewind_runtime.py tests/test_rewind_tui_integration.py tests/test_rewind_plugin_integration.py
git commit -m "集成回溯预览：验证快照链并接通 TUI"
```

## Task 9: Reconcile documentation and run the full acceptance suite

**Stage:** Integration

**Files:**
- Modify: `docs/superpowers/plans/2026-07-16-cli-tui-p0-recovery-review-plan.md`
- Modify: `README.md`
- Modify: `docs/amp-inspired-runtime.md`
- Modify: `docs/research/cli-tui-design-comparison.md`
- Modify: `code_agent_win/AGENTS.md`

- [ ] **Step 1: Run every Feature suite**

```powershell
$featureTests = @(
  'src/code_agent/sessions/tests',
  'src/code_agent/workspace/tests',
  'src/code_agent/core/tests',
  'src/code_agent/interfaces/tests'
)
foreach ($tests in $featureTests) {
  & $python -m unittest discover -s $tests -p 'test_*.py' -v
  if ($LASTEXITCODE -ne 0) { throw "failed suite: $tests" }
}
```

Expected: zero failures/errors; platform skips remain explicit.

- [ ] **Step 2: Run the root suite and structural checks**

```powershell
& $python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'failed root integration suite' }
& $python -m compileall -q src code_agent_win tests
if ($LASTEXITCODE -ne 0) { throw 'compileall failed' }
git diff --check
git status --short
```

Expected: all tests pass, compile succeeds, no whitespace errors, and only
Task 9 documentation changes remain.

- [ ] **Step 3: Update only verified claims**

Document:

- `/rewind list` candidates are not availability promises;
- `/rewind preview` is read-only and as-of bounded;
- conversation/code/both prerequisites;
- v10 conversation-only behavior;
- unknown writers permanently invalidate P0 code coverage until a future
  trusted rebaseline design;
- snapshot and gate product-state locations;
- no apply, no Git reset/checkout, no index/HEAD restoration;
- exact platform skips and tested commands.

Mark Task 7 complete in the parent roadmap only if every acceptance command
above passed. Keep Task 8 status aligned with the actual documentation/full
suite result.

- [ ] **Step 4: Commit documentation reconciliation**

```powershell
git add docs/superpowers/plans/2026-07-16-cli-tui-p0-recovery-review-plan.md README.md docs/amp-inspired-runtime.md docs/research/cli-tui-design-comparison.md code_agent_win/AGENTS.md
git commit -m "对齐回溯交付：记录 P0 可信预览边界"
```

## Final acceptance gates

- Every production behavior was preceded by an observed failing test.
- Requirement boundary commits contain no code or Units changes.
- Each Feature implementation modifies only its own Feature directory.
- Integration tasks modify no `src/` file.
- v10 migration fabricates no code rewind facts.
- Conversation count is thread-filtered in one read transaction.
- Checkpoint and mutation high-water share one cross-process total order.
- Known writes persist preimage before filesystem mutation.
- Unknown writers invalidate coverage before execution and never auto-reenable.
- Child actions retain root owner and child origin.
- Snapshot missing/tamper/foreign-workspace states are distinct.
- Current user or overlapping foreign-thread edits disable code/both.
- Candidate list never claims final availability.
- Preview performs no provider, action, approval, Sessions mutation, file
  restore, Git reset/checkout, index, or HEAD operation.
- Full Feature and root suites pass from the isolated worktree.
