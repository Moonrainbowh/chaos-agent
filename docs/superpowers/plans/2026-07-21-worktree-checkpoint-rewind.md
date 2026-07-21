# Worktree Checkpoint Rewind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each durable foreground task in a managed Git worktree and let users restore code, session state, or both from persistent checkpoints without modifying the source worktree.

**Architecture:** Workspace owns fixed Git worktree operations, eligible-file inventory, byte snapshots, and a content-addressed blob store. Sessions owns lineage, manifest, cursor, and rewind-operation facts. A new Checkpoints Feature coordinates pause/capture/preview/restore as a recoverable Saga; Interfaces only renders and delegates, while root composition switches workspace-scoped services to the managed task root.

**Tech Stack:** Python 3.10+, asyncio, SQLite migrations, Git fixed-argument subprocesses, SHA-256 content addressing, unittest, Windows-first filesystem behavior.

## Global Constraints

- The user's source worktree bytes and Git index must never be modified by task execution or Rewind.
- Capture only tracked files and non-ignored untracked files that pass path and sensitive-file policy.
- Never capture ignored paths, `.env`, private keys, build caches, `.git` metadata, symlinks, junctions, reparse points, or paths outside the worktree.
- Do not replay an in-flight command after resume or crash recovery.
- Code restore defaults to confirmation No and must revalidate the preview fingerprint.
- Preserve old messages, events, checkpoints, and audit records; conversation rewind is a non-destructive fork.
- Rewind never resets cumulative budget usage.
- Files remain at most 300 lines, functions at most 50 lines, and each Feature stays within 10 principal Units.
- Follow requirement → Feature implementation → root integration phases; never modify `code_agent_win` during Feature implementation.

---

## File Structure

- Create `src/code_agent/checkpoints/AGENTS.md`: Checkpoints Feature contract.
- Create `src/code_agent/checkpoints/models.py`: modes, previews, operation states, and results.
- Create `src/code_agent/checkpoints/service.py`: checkpoint capture service.
- Create `src/code_agent/checkpoints/rewind.py`: recoverable Rewind coordinator.
- Create `src/code_agent/checkpoints/tests/test_service.py`: capture tests.
- Create `src/code_agent/checkpoints/tests/test_rewind.py`: Saga tests.
- Create `src/code_agent/workspace/inventory.py`: fixed Git eligible-file inventory and digest.
- Create `src/code_agent/workspace/snapshot_store.py`: content-addressed blobs and manifests.
- Create `src/code_agent/workspace/worktrees.py`: fixed managed-worktree lifecycle and lease-neutral facts.
- Modify `src/code_agent/workspace/edits.py`: restore plan and preflight support around `WorkspaceSnapshot.restore`.
- Modify `src/code_agent/workspace/git.py`: fixed `ls-files`, common-dir, HEAD, and worktree commands.
- Create or modify corresponding Workspace tests.
- Create `src/code_agent/sessions/workspace_models.py`: persisted lineage/snapshot/rewind records.
- Create `src/code_agent/sessions/_workspace_snapshots.py`: transactional repository operations.
- Modify `src/code_agent/sessions/_database.py`: schema migration 14 and required columns.
- Modify `src/code_agent/sessions/repository.py`: compose the workspace snapshot repository mixin.
- Modify Sessions migration/model/repository tests.
- Modify `src/code_agent/core/task.py`: terminal `TaskStatus.SUPERSEDED`.
- Create `src/code_agent/interfaces/checkpoint_control.py`: UI-neutral checkpoint/rewind controller.
- Modify command registry, parser, Picker, TUI delegation, and their tests.
- Create `code_agent_win/workspace_runtime.py`: root integration owner for lineage and workspace-scoped service composition.
- Modify `code_agent_win/app.py`, `code_agent_win/host_composition.py`, `code_agent_win/application_context.py`, `code_agent_win/app_ui.py`, and root integration tests.

### Task 1: Lock cross-Feature requirement contracts

**Files:**
- Create: `src/code_agent/checkpoints/AGENTS.md`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-07-21-worktree-checkpoint-rewind-design.md`.
- Produces: goal and boundary contracts only; no new Units during this requirement stage.

- [ ] **Step 1: Create the Checkpoints requirement contract**

```markdown
# Checkpoints
协调 durable checkpoint 的工作区捕获、预览、三模式 Rewind 与崩溃恢复。

## 边界
- 负责：在命令树已终止且持有 lineage lease 时协调 Workspace 与 Sessions 创建可恢复 checkpoint。
- 负责：提供仅代码、仅会话、代码与会话三种非破坏性 Rewind，并以 pre-rewind checkpoint 和持久 intent 实现故障回滚。
- 负责：恢复前生成有界预览、要求显式确认并复验工作区 fingerprint。
- 不负责：实现 Git、路径防护、blob 存储、SQLite 细节、终端渲染或模型调用。
- 不负责：重放命令、删除旧历史、重置预算、修改 source worktree 或把多文件恢复宣称为原子事务。

## Units
```

- [ ] **Step 2: Add requirement bullets to existing Features**

Add exact boundaries:

```markdown
# Workspace
- 负责：创建受管任务 worktree，枚举合规 tracked/untracked 文件，并持久化内容寻址快照。
- 不负责：决定 checkpoint 业务语义、用户确认、会话分叉或 Rewind 状态。

# Sessions
- 负责：原子持久化 lineage、snapshot manifest、checkpoint 游标与 rewind operation，并提供非破坏性任务分叉。
- 不负责：读取文件、执行 Git、恢复工作树或决定用户授权。

# Core
- 负责：提供终态 `SUPERSEDED`，阻止被 Rewind 替代的旧任务再次执行。

# Interfaces
- 负责：展示 checkpoint Picker、三种 Rewind、预览、确认、进度与 recovery-required 状态，并只委托 Controller。
```

- [ ] **Step 3: Validate and commit the requirement stage**

Run: `git diff --check -- src/code_agent/checkpoints/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/sessions/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md`

Expected: exit code 0.

```powershell
git add -- src/code_agent/checkpoints/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/sessions/AGENTS.md src/code_agent/core/AGENTS.md src/code_agent/interfaces/AGENTS.md
git commit -m "明确 Worktree Rewind 跨 Feature 边界" -m "- 变更内容：定义 Workspace、Sessions、Core、Interfaces 与 Checkpoints 职责。" -m "- 变更原因：在实现前锁定恢复与持久化契约。" -m "- 验证情况：git diff --check 通过。"
```

### Task 2: Enumerate eligible code state and build restore plans

**Files:**
- Create: `src/code_agent/workspace/inventory.py`
- Modify: `src/code_agent/workspace/git.py`
- Modify: `src/code_agent/workspace/edits.py`
- Create: `src/code_agent/workspace/tests/test_inventory.py`
- Modify: `src/code_agent/workspace/tests/test_edits.py`
- Modify: `src/code_agent/workspace/tests/test_git.py`
- Modify: `src/code_agent/workspace/AGENTS.md`

**Interfaces:**
- Produces: `InventoryEntry`, `WorkspaceInventory`, `GitWorkspace.snapshot_paths()`, `build_restore_snapshot(current, target) -> WorkspaceSnapshot`, `workspace_fingerprint(inventory) -> str`.
- Consumes: `WorkspacePathGuard`, `WorkspaceEditor.snapshot`, fixed Git invocation limits.

- [ ] **Step 1: Write failing inventory and restore tests**

```python
def test_inventory_includes_tracked_and_nonignored_untracked_only(self) -> None:
    self.git("init")
    self.write("tracked.py", "old")
    self.git("add", "tracked.py")
    self.git("commit", "-m", "base")
    self.write("visible.py", "new")
    self.write("ignored.tmp", "cache")
    self.write(".gitignore", "*.tmp\n")
    inventory = WorkspaceInventory.capture(self.root, self.guard, GitWorkspace(self.root))
    self.assertEqual(inventory.paths, (".gitignore", "tracked.py", "visible.py"))

def test_restore_snapshot_removes_files_created_after_checkpoint(self) -> None:
    target = WorkspaceSnapshot((SnapshotEntry("kept.py", b"old", True),))
    current = ("created.py", "kept.py")
    restore = build_restore_snapshot(current, target)
    self.assertEqual(
        [(entry.relative_path, entry.existed) for entry in restore.entries],
        [("created.py", False), ("kept.py", True)],
    )
```

Also add cases for deleted tracked paths, ignored directories, `.env`, private-key names, `.git`, symlink/reparse rejection, duplicate paths, file-count/byte/deadline limits, and deterministic digest ordering.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.workspace.tests.test_inventory src.code_agent.workspace.tests.test_edits src.code_agent.workspace.tests.test_git -v`

Expected: FAIL because inventory and restore-plan APIs do not exist.

- [ ] **Step 3: Implement fixed inventory APIs**

Add a fixed Git operation:

```python
def snapshot_paths(self) -> tuple[str, ...]:
    result = self._invoke(
        "snapshot_paths",
        ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
    )
    self._require_success("snapshot_paths", result)
    return tuple(sorted(_decode_path_list(result.stdout)))

def _decode_path_list(value: bytes) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        return tuple(part.decode("utf-8") for part in value.split(b"\0") if part)
    except UnicodeDecodeError as error:
        raise GitCommandError(
            "snapshot_paths", (), None, "invalid UTF-8 path",
            "git snapshot_paths returned an undecodable path",
        ) from error
```

Create focused immutable types:

```python
@dataclass(frozen=True)
class InventoryEntry:
    relative_path: str
    size: int
    sha256: str
    mode: int

@dataclass(frozen=True)
class WorkspaceInventory:
    entries: tuple[InventoryEntry, ...]
    digest: str

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.relative_path for entry in self.entries)
```

Resolve every Git path through `WorkspacePathGuard`, reject non-regular/link/reparse paths, apply existing sensitive-name rules, enforce limits, hash bytes in sorted path order, and calculate one canonical manifest digest.

Build the restore snapshot with tombstones for `current_paths - target_paths`; perform all path/blob/parent checks before invoking `WorkspaceEditor.restore`.

- [ ] **Step 4: Run Workspace tests**

Run: `uv run --with regex python -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v`

Expected: all Workspace tests pass.

- [ ] **Step 5: Update Units and commit**

```markdown
- `WorkspaceInventory.capture(...)`、`workspace_fingerprint(...)`：枚举并摘要合规 tracked/untracked 代码状态 | 有界 Git 与文件读取 | 排除 ignored、敏感、链接/reparse 与越界路径
- `build_restore_snapshot(current_paths, target)`：为完整目标 manifest 补充新增文件 tombstone | 无副作用 | 所有路径在恢复前预检
```

```powershell
git add -- src/code_agent/workspace/AGENTS.md src/code_agent/workspace/inventory.py src/code_agent/workspace/git.py src/code_agent/workspace/edits.py src/code_agent/workspace/tests/test_inventory.py src/code_agent/workspace/tests/test_edits.py src/code_agent/workspace/tests/test_git.py
git commit -m "增加工作区快照清单" -m "- 变更内容：枚举合规代码文件并生成完整恢复计划。" -m "- 变更原因：为持久 checkpoint 提供确定性捕获边界。" -m "- 验证情况：Workspace Feature 测试通过。"
```

### Task 3: Persist content-addressed snapshot blobs

**Files:**
- Create: `src/code_agent/workspace/snapshot_store.py`
- Create: `src/code_agent/workspace/tests/test_snapshot_store.py`
- Modify: `src/code_agent/workspace/AGENTS.md`

**Interfaces:**
- Produces: `BlobRef`, `SnapshotManifestEntry`, `SnapshotManifest`, `MaterializedSnapshot`, `ContentAddressedSnapshotStore.put(snapshot, modes)`, `.materialize(manifest)` and `.delete_orphans(referenced, older_than)`.
- Consumes: `WorkspaceSnapshot`, SHA-256, atomic same-directory file replacement.

- [ ] **Step 1: Write failing blob-store tests**

```python
def test_identical_content_is_stored_once_and_materializes(self) -> None:
    store = ContentAddressedSnapshotStore(self.root / "store")
    snapshot = WorkspaceSnapshot((
        SnapshotEntry("one.py", b"same", True),
        SnapshotEntry("two.py", b"same", True),
    ))
    manifest = store.put(snapshot, {"one.py": 0o644, "two.py": 0o644})
    self.assertEqual(manifest.entries[0].blob_sha256, manifest.entries[1].blob_sha256)
    blobs = tuple(path for path in (self.root / "store" / "blobs").rglob("*") if path.is_file())
    self.assertEqual(len(blobs), 1)
    self.assertEqual(store.materialize(manifest).snapshot, snapshot)

def test_corrupt_blob_fails_before_restore(self) -> None:
    store = ContentAddressedSnapshotStore(self.root / "store")
    manifest = store.put(WorkspaceSnapshot((SnapshotEntry("a.py", b"ok", True),)), {"a.py": 0o644})
    store.blob_path(manifest.entries[0].blob_sha256).write_bytes(b"bad")
    with self.assertRaises(SnapshotIntegrityError):
        store.materialize(manifest)
```

Add atomic-publish failure cleanup, byte/file limits, tombstone entries, deterministic manifest digest, and orphan-GC retention tests.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.workspace.tests.test_snapshot_store -v`

Expected: FAIL because `snapshot_store` does not exist.

- [ ] **Step 3: Implement the blob store**

Use immutable records:

```python
@dataclass(frozen=True)
class SnapshotManifestEntry:
    relative_path: str
    existed: bool
    blob_sha256: str | None
    size: int
    mode: int | None

@dataclass(frozen=True)
class SnapshotManifest:
    entries: tuple[SnapshotManifestEntry, ...]
    inventory_digest: str
    total_bytes: int

@dataclass(frozen=True)
class MaterializedSnapshot:
    snapshot: WorkspaceSnapshot
    modes: Mapping[str, int]
```

Write missing blobs to `.tmp-<uuid>` in the final shard directory, flush and `os.fsync`, verify the digest, then `os.replace`. `materialize` must verify size and digest for every blob before returning `MaterializedSnapshot`; the restore path applies its `modes` only after each corresponding byte restore succeeds.

- [ ] **Step 4: Run tests, update Units, and commit**

Run: `uv run --with regex python -m unittest src.code_agent.workspace.tests.test_snapshot_store -v`

Expected: all tests pass.

```powershell
git add -- src/code_agent/workspace/AGENTS.md src/code_agent/workspace/snapshot_store.py src/code_agent/workspace/tests/test_snapshot_store.py
git commit -m "增加内容寻址快照存储" -m "- 变更内容：原子发布、校验、去重和回收工作区 blob。" -m "- 变更原因：让 checkpoint 跨进程持久恢复且避免 SQLite 膨胀。" -m "- 验证情况：SnapshotStore 定向测试通过。"
```

### Task 4: Manage isolated task worktrees

**Files:**
- Create: `src/code_agent/workspace/worktrees.py`
- Create: `src/code_agent/workspace/tests/test_worktrees.py`
- Modify: `src/code_agent/workspace/git.py`
- Modify: `src/code_agent/workspace/AGENTS.md`

**Interfaces:**
- Produces: `RepositoryIdentity`, `ManagedWorktree`, `WorktreeManager.create(source_root, lineage_id, branch_name)`, `.remove(worktree, confirmed)`, `.prune(records)`.
- Consumes: fixed Git common-dir/HEAD/worktree operations and the Task 2/3 snapshot APIs for later seeding.

- [ ] **Step 1: Write failing worktree tests**

```python
def test_create_uses_fixed_branch_and_does_not_change_source(self) -> None:
    before_status = self.git.status_porcelain()
    manager = WorktreeManager(self.storage_root)
    created = manager.create(self.source, "lineage-1", "codex/task-lineage-1")
    self.assertTrue((created.root / ".git").exists())
    self.assertEqual(self.git.status_porcelain(), before_status)
    self.assertEqual(created.source_root, self.source.resolve())

def test_remove_refuses_active_or_unconfirmed_worktree(self) -> None:
    created = self.manager.create(self.source, "lineage-1", "codex/task-lineage-1")
    with self.assertRaises(WorkspaceError):
        self.manager.remove(created, confirmed=False, active=True)
```

Add repository identity stability, branch collision, existing directory, path containment, long path, dirty managed worktree, and missing Git tests.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.workspace.tests.test_worktrees -v`

Expected: FAIL because `WorktreeManager` does not exist.

- [ ] **Step 3: Implement fixed worktree commands**

The only creation command must be constructed locally:

```python
arguments = (
    "worktree", "add", "-b", branch_name,
    str(target_root), head_commit,
)
```

Validate `branch_name` against `codex/task-[a-z0-9-]+`, require target containment under the configured storage root, and derive `repository_id` from the resolved `--git-common-dir`. Do not accept model-provided Git flags.

- [ ] **Step 4: Run tests, update Units, and commit**

Run: `uv run --with regex python -m unittest src.code_agent.workspace.tests.test_worktrees -v`

Expected: all tests pass.

```powershell
git add -- src/code_agent/workspace/AGENTS.md src/code_agent/workspace/worktrees.py src/code_agent/workspace/git.py src/code_agent/workspace/tests/test_worktrees.py
git commit -m "增加任务 Worktree 管理器" -m "- 变更内容：以固定 Git 参数创建、识别和保护任务 worktree。" -m "- 变更原因：隔离 Agent 改动并保护 source worktree。" -m "- 验证情况：WorktreeManager 定向测试通过。"
```

### Task 5: Persist lineages, manifests, cursors, and Rewind operations

**Files:**
- Create: `src/code_agent/sessions/workspace_models.py`
- Create: `src/code_agent/sessions/_workspace_snapshots.py`
- Modify: `src/code_agent/sessions/_database.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/sessions/_records.py`
- Create: `src/code_agent/sessions/tests/test_workspace_snapshots.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`
- Modify: `src/code_agent/sessions/AGENTS.md`

**Interfaces:**
- Produces: `WorkspaceLineageRecord`, `WorkspaceSnapshotRecord`, `CheckpointCursor`, `RewindOperationRecord`, repository methods `create_lineage`, `publish_workspace_checkpoint`, `load_workspace_snapshot`, `begin_rewind`, `complete_rewind`, `fail_rewind`, `pending_rewinds`, `fork_task_from_checkpoint`, `transfer_lineage_owner`.
- Consumes: JSON-safe manifest records from Task 3 and existing SQLite write serialization.

- [ ] **Step 1: Write failing migration and repository tests**

```python
async def test_workspace_snapshot_and_checkpoint_publish_atomically(self) -> None:
    thread_id = await self.repository.create_thread()
    lineage = WorkspaceLineageRecord.create(
        repository_id="repo", source_root="C:/source", worktree_root="C:/managed",
        branch_name="codex/task-one", head_commit="a" * 40, owner_task_id=None,
    )
    await self.repository.create_lineage(lineage)
    snapshot = _snapshot_record(lineage.id)
    checkpoint = await self.repository.publish_workspace_checkpoint(
        thread_id, "paused", {"task_id": "task-1"}, snapshot, _cursor()
    )
    self.assertEqual((await self.repository.load_workspace_snapshot(checkpoint)).id, snapshot.id)

async def test_task_fork_preserves_budget_and_excludes_later_messages(self) -> None:
    checkpoint_id = await self._checkpoint_after_message("before")
    await self.repository.append_message(self.thread_id, Message(role="user", content="after"))
    forked = await self.repository.fork_task_from_checkpoint(checkpoint_id)
    history = await self.repository.load_messages(forked.thread_id)
    self.assertEqual([item.message.content for item in history], ["before"])
    self.assertGreaterEqual((await self.repository.load_task_budget(forked.thread_id)).tool_calls, 0)
```

Add transaction rollback, foreign keys, missing blobs-as-metadata-only, owner transfer, pending operation, illegal completion, budget non-reset, and old-schema migration tests.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.sessions.tests.test_migrations src.code_agent.sessions.tests.test_workspace_snapshots -v`

Expected: FAIL because schema version 14 and repository APIs do not exist.

- [ ] **Step 3: Add schema version 14**

Add normalized tables with foreign keys:

```sql
CREATE TABLE workspace_lineages (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL,
  source_root TEXT NOT NULL,
  worktree_root TEXT NOT NULL UNIQUE,
  branch_name TEXT NOT NULL,
  head_commit TEXT NOT NULL,
  owner_task_id TEXT REFERENCES tasks(id),
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
ALTER TABLE tasks ADD COLUMN workspace_lineage_id TEXT REFERENCES workspace_lineages(id);
CREATE TABLE workspace_snapshots (
  id TEXT PRIMARY KEY,
  lineage_id TEXT NOT NULL REFERENCES workspace_lineages(id),
  inventory_digest TEXT NOT NULL,
  total_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE workspace_snapshot_entries (
  snapshot_id TEXT NOT NULL REFERENCES workspace_snapshots(id) ON DELETE CASCADE,
  relative_path TEXT NOT NULL,
  existed INTEGER NOT NULL,
  blob_sha256 TEXT,
  size INTEGER NOT NULL,
  mode INTEGER,
  PRIMARY KEY(snapshot_id, relative_path)
);
CREATE TABLE checkpoint_workspace_state (
  checkpoint_id TEXT PRIMARY KEY REFERENCES checkpoints(id) ON DELETE CASCADE,
  snapshot_id TEXT REFERENCES workspace_snapshots(id),
  message_sequence INTEGER NOT NULL,
  event_sequence INTEGER NOT NULL,
  goals_payload TEXT NOT NULL,
  task_state_payload TEXT NOT NULL,
  budget_payload TEXT NOT NULL,
  snapshot_status TEXT NOT NULL
);
CREATE TABLE rewind_operations (
  id TEXT PRIMARY KEY,
  lineage_id TEXT NOT NULL REFERENCES workspace_lineages(id),
  source_checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
  rollback_checkpoint_id TEXT REFERENCES checkpoints(id),
  mode TEXT NOT NULL,
  preview_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Add indexes for lineage status and pending rewind queries, and update `_REQUIRED_COLUMNS`.

- [ ] **Step 4: Implement typed records and atomic repository methods**

Use enums with fixed wire values:

```python
class RewindMode(str, Enum):
    CODE = "code"
    SESSION = "session"
    CODE_AND_SESSION = "code_and_session"

class RewindOperationStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    RECOVERY_REQUIRED = "recovery_required"
```

`publish_workspace_checkpoint` must insert snapshot, entries, checkpoint, and cursor in one `_database.write` callback. `fork_task_from_checkpoint` must copy only records at or before stored sequences and must carry budget usage forward.

- [ ] **Step 5: Run Sessions tests, update Units, and commit**

Run: `uv run --with regex python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v`

Expected: all Sessions tests pass.

```powershell
git add -- src/code_agent/sessions/AGENTS.md src/code_agent/sessions/workspace_models.py src/code_agent/sessions/_workspace_snapshots.py src/code_agent/sessions/_database.py src/code_agent/sessions/repository.py src/code_agent/sessions/_records.py src/code_agent/sessions/tests/test_workspace_snapshots.py src/code_agent/sessions/tests/test_migrations.py
git commit -m "持久化 Worktree Checkpoint 事实" -m "- 变更内容：保存 lineage、manifest、游标、Rewind operation 与非破坏性任务分叉。" -m "- 变更原因：让代码和会话恢复跨进程可审计。" -m "- 验证情况：Sessions Feature 测试通过。"
```

### Task 6: Implement checkpoint capture and recoverable Rewind Saga

**Files:**
- Create: `src/code_agent/checkpoints/models.py`
- Create: `src/code_agent/checkpoints/service.py`
- Create: `src/code_agent/checkpoints/rewind.py`
- Create: `src/code_agent/checkpoints/tests/test_service.py`
- Create: `src/code_agent/checkpoints/tests/test_rewind.py`
- Modify: `src/code_agent/checkpoints/AGENTS.md`
- Modify: `src/code_agent/core/task.py`
- Modify: `src/code_agent/core/tests/test_task.py`
- Modify: `src/code_agent/core/AGENTS.md`

**Interfaces:**
- Produces: `CheckpointService.capture(task_id, label) -> CheckpointRecord`, `RewindCoordinator.preview(task_id, checkpoint_id, mode) -> RewindPreview`, `execute(preview, confirmed) -> RewindResult`, `recover_pending() -> tuple[RewindResult, ...]`.
- Consumes: task pause/command termination port, Workspace inventory/blob/restore APIs, Sessions methods from Task 5, cache invalidation callback.

- [ ] **Step 1: Write failing capture and Saga tests**

```python
async def test_capture_waits_for_process_termination_before_inventory(self) -> None:
    order: list[str] = []
    service = CheckpointService(
        sessions=self.sessions,
        workspace=self.workspace,
        quiesce=lambda task_id: _record(order, "quiesce"),
    )
    await service.capture("task-1", "paused")
    self.assertEqual(order[:2], ["quiesce", "inventory"])

async def test_stale_preview_is_rejected_without_restore(self) -> None:
    preview = await self.coordinator.preview("task-1", "cp-1", RewindMode.CODE)
    self.workspace.fingerprint = "changed"
    with self.assertRaisesRegex(RewindConflict, "preview is stale"):
        await self.coordinator.execute(preview, confirmed=True)
    self.assertEqual(self.workspace.restore_calls, [])

async def test_combined_rewind_rolls_back_code_when_session_fork_fails(self) -> None:
    self.sessions.fail_fork = True
    preview = await self.coordinator.preview("task-1", "cp-1", RewindMode.CODE_AND_SESSION)
    with self.assertRaises(RewindError):
        await self.coordinator.execute(preview, confirmed=True)
    self.assertEqual(self.workspace.restored_snapshot_ids, ["target", "pre-rewind"])
```

Add unconfirmed no-op, code-only, session-only, combined success, blob corruption, restore failure, rollback failure → recovery-required, startup pending recovery, cache invalidation, and Verification invalidation tests.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.checkpoints.tests.test_service src.code_agent.checkpoints.tests.test_rewind src.code_agent.core.tests.test_task -v`

Expected: FAIL because Checkpoints Feature and `SUPERSEDED` do not exist.

- [ ] **Step 3: Add the superseded terminal state**

```python
class TaskStatus(str, Enum):
    # existing values
    SUPERSEDED = "superseded"

_TERMINAL = {
    TaskStatus.COMPLETED,
    TaskStatus.ACCEPTED_PARTIAL,
    TaskStatus.FAILED,
    TaskStatus.SUPERSEDED,
}
```

Permit active, paused, interrupted, and waiting tasks to transition to `SUPERSEDED`; never permit transitions out.

- [ ] **Step 4: Implement capture and preview**

Define immutable public models:

```python
@dataclass(frozen=True)
class RewindPreview:
    operation_id: str
    task_id: str
    lineage_id: str
    checkpoint_id: str
    mode: RewindMode
    fingerprint: str
    restore_count: int
    delete_count: int
    total_bytes: int
    paths: tuple[str, ...]
    code_available: bool

@dataclass(frozen=True)
class RewindResult:
    operation_id: str
    task_id: str
    replacement_task_id: str | None
    status: RewindOperationStatus
```

`capture` must call `quiesce`, acquire the lineage lock, inventory, snapshot, blob store, then atomically publish through Sessions. On capture limit failure, publish the checkpoint with `snapshot_status="unavailable"` and no snapshot ID.

- [ ] **Step 5: Implement the Saga**

Use explicit compensating operations:

```python
async def execute(self, preview: RewindPreview, *, confirmed: bool) -> RewindResult:
    if not confirmed:
        raise RewindConfirmationRequired("rewind requires confirmation")
    async with self._locks.for_lineage(preview.lineage_id):
        await self._quiesce(preview.task_id)
        rollback = await self._checkpoints.capture(preview.task_id, "pre-rewind")
        operation = await self._sessions.begin_rewind(preview, rollback.id)
        try:
            await self._require_fingerprint(preview.fingerprint)
            replacement = await self._apply_mode(preview)
            await self._sessions.complete_rewind(operation.id, replacement)
            self._invalidate_cache(())
            return RewindResult(operation.id, preview.task_id, replacement, RewindOperationStatus.COMPLETED)
        except Exception as error:
            await self._rollback(operation, rollback, error)
            raise
```

`recover_pending` always restores the rollback checkpoint; it never attempts to resume forward progress or replay a command. Rollback failure records `RECOVERY_REQUIRED` and causes every workspace write/command port to reject the lineage.

- [ ] **Step 6: Run tests, update Units, and commit**

Run: `uv run --with regex python -m unittest discover -s src/code_agent/checkpoints/tests -p 'test_*.py' -v`

Expected: all Checkpoints tests pass.

Run: `uv run --with regex python -m unittest src.code_agent.core.tests.test_task -v`

Expected: all Core task tests pass.

Update `src/code_agent/checkpoints/AGENTS.md` Units with the four public models/services and update the Core TaskStatus Unit.

```powershell
git add -- src/code_agent/checkpoints src/code_agent/core/AGENTS.md src/code_agent/core/task.py src/code_agent/core/tests/test_task.py
git commit -m "实现 Checkpoint Rewind Saga" -m "- 变更内容：捕获快照、预览三种模式，并以 pre-rewind 补偿处理失败与崩溃。" -m "- 变更原因：提供可审计且失败闭合的代码与会话恢复。" -m "- 验证情况：Checkpoints 与 Core Task 定向测试通过。"
```

### Task 7: Add checkpoint and Rewind user controls

**Files:**
- Create: `src/code_agent/interfaces/checkpoint_control.py`
- Create: `src/code_agent/interfaces/tests/test_checkpoint_control.py`
- Modify: `src/code_agent/interfaces/command_registry.py`
- Modify: `src/code_agent/interfaces/tui_commands.py`
- Modify: `src/code_agent/interfaces/tui_builtin_commands.py`
- Modify: `src/code_agent/interfaces/tui_interactions.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Modify: `src/code_agent/interfaces/tests/test_windows_tui.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`

**Interfaces:**
- Produces: `CheckpointControl.list`, `.create`, `.preview_rewind`, `.execute_rewind`; commands `/checkpoint list`, `/checkpoint create [label]`, `/rewind [checkpoint-id]`.
- Consumes: `CheckpointService` and `RewindCoordinator` protocols only.

- [ ] **Step 1: Write failing control and command tests**

```python
async def test_rewind_requires_preview_mode_and_explicit_confirmation(self) -> None:
    control = CheckpointControl(self.checkpoints, self.rewind)
    preview = await control.preview_rewind("task-1", "cp-1", "code")
    self.assertEqual(preview.mode, RewindMode.CODE)
    with self.assertRaises(RewindConfirmationRequired):
        await control.execute_rewind(preview, confirmed=False)

def test_rewind_command_defaults_to_checkpoint_picker(self) -> None:
    parsed = parse_tui_command("/rewind", available_services={"checkpoints"})
    self.assertEqual(parsed.command.kind, TuiCommandKind.REWIND)
    self.assertIsNone(parsed.command.instruction)
```

Add disabled reasons for unavailable snapshots, default code mode, three mode choices, bounded preview rows, confirmation default No, recovery-required rendering, Chinese/English aliases, and JSON-safe result tests.

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --with regex python -m unittest src.code_agent.interfaces.tests.test_checkpoint_control src.code_agent.interfaces.tests.test_tui_commands src.code_agent.interfaces.tests.test_windows_tui -v`

Expected: FAIL because checkpoint controls and commands do not exist.

- [ ] **Step 3: Implement UI-neutral control**

```python
class CheckpointControl:
    def __init__(self, checkpoints: CheckpointServicePort, rewind: RewindPort) -> None:
        self._checkpoints = checkpoints
        self._rewind = rewind

    async def preview_rewind(
        self, task_id: str, checkpoint_id: str, mode: str = "code"
    ) -> RewindPreview:
        return await self._rewind.preview(task_id, checkpoint_id, RewindMode(mode))

    async def execute_rewind(
        self, preview: RewindPreview, *, confirmed: bool
    ) -> RewindResult:
        return await self._rewind.execute(preview, confirmed=confirmed)
```

Define the narrow ports in the same file so Interfaces does not import concrete coordinator internals:

```python
class CheckpointServicePort(Protocol):
    async def list(self, task_id: str) -> tuple[CheckpointRecord, ...]: ...
    async def capture(self, task_id: str, label: str) -> CheckpointRecord: ...

class RewindPort(Protocol):
    async def preview(
        self, task_id: str, checkpoint_id: str, mode: RewindMode
    ) -> RewindPreview: ...
    async def execute(
        self, preview: RewindPreview, *, confirmed: bool
    ) -> RewindResult: ...
```

The TUI flow is Picker → mode Picker → bounded preview → Host confirmation → progress/result entry. The command handler never calls Workspace, Git, SQLite, or Runtime directly.

- [ ] **Step 4: Run tests, update Units, and commit**

Run: `uv run --with regex python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v`

Expected: all Interfaces tests pass.

```powershell
git add -- src/code_agent/interfaces/AGENTS.md src/code_agent/interfaces/checkpoint_control.py src/code_agent/interfaces/command_registry.py src/code_agent/interfaces/tui_commands.py src/code_agent/interfaces/tui_builtin_commands.py src/code_agent/interfaces/tui_interactions.py src/code_agent/interfaces/windows_tui.py src/code_agent/interfaces/tests/test_checkpoint_control.py src/code_agent/interfaces/tests/test_tui_commands.py src/code_agent/interfaces/tests/test_windows_tui.py
git commit -m "增加 Checkpoint Rewind 控制面" -m "- 变更内容：接入 checkpoint 列表、创建、三模式预览与确认。" -m "- 变更原因：让恢复能力通过统一命令和 Picker 安全暴露。" -m "- 验证情况：Interfaces Feature 测试通过。"
```

### Task 8: Integrate managed workspace runtime at the application root

**Files:**
- Create: `code_agent_win/workspace_runtime.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/host_composition.py`
- Modify: `code_agent_win/application_context.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_command_integration.py`

**Interfaces:**
- Produces: `ManagedWorkspaceRuntime.prepare_task(source_root, task_id) -> TaskWorkspace`, `.services(task_workspace) -> WorkspaceServices`, `.recover_pending()`.
- Consumes: all Feature APIs from Tasks 2–7.

- [ ] **Step 1: Write failing root integration tests**

```python
async def test_new_task_uses_managed_worktree_and_preserves_source(self) -> None:
    source_before = _tree_digest(self.root)
    application = create_application(self.root)
    task = await application.tasks.start("edit note.py")
    self.assertNotEqual(Path(task.contract.authorization.workspace_root), self.root)
    self.assertTrue(Path(task.contract.authorization.workspace_root).is_relative_to(self.managed_root))
    self.assertEqual(_tree_digest(self.root), source_before)

async def test_all_workspace_scoped_services_use_task_root(self) -> None:
    application = create_application(self.root)
    task = await application.tasks.start("inspect")
    task_root = Path(task.contract.authorization.workspace_root)
    self.assertEqual(application.workspace_root_for(task.id), task_root)
    self.assertEqual(application.runtime_root_for(task.id), task_root)
    self.assertEqual(application.verification_root_for(task.id), task_root)
```

Add dirty-source seeding, source index preservation, child Agent root inheritance, `/diff`, Context Builder, Runtime, Verification, checkpoint-after-restart, pending recovery, and non-Git workspace graceful-unavailable tests.

- [ ] **Step 2: Run root tests and confirm failure**

Run: `uv run --with regex python -m unittest tests.test_agent_app tests.test_command_integration -v`

Expected: FAIL because applications still compose services at the source root.

- [ ] **Step 3: Create the integration owner**

```python
@dataclass(frozen=True)
class TaskWorkspace:
    lineage_id: str
    source_root: Path
    worktree_root: Path
    branch_name: str

class ManagedWorkspaceRuntime:
    async def prepare_task(self, source_root: Path, task_id: str) -> TaskWorkspace:
        lineage_id = uuid.uuid4().hex
        managed = await asyncio.to_thread(
            self._worktrees.create,
            source_root,
            lineage_id,
            f"codex/task-{task_id[:12]}",
        )
        await self._seed_source_changes(source_root, managed.root)
        record = WorkspaceLineageRecord(
            id=lineage_id,
            repository_id=managed.repository_id,
            source_root=str(source_root),
            worktree_root=str(managed.root),
            branch_name=managed.branch_name,
            head_commit=managed.head_commit,
            owner_task_id=task_id,
            status=WorkspaceLineageStatus.ACTIVE,
        )
        await self._sessions.create_lineage(record)
        return TaskWorkspace(lineage_id, source_root, managed.root, managed.branch_name)

    async def _seed_source_changes(self, source_root: Path, target_root: Path) -> None:
        source_guard = WorkspacePathGuard(source_root)
        source_git = GitWorkspace(source_root)
        paths = await asyncio.to_thread(source_git.snapshot_paths)
        snapshot = await asyncio.to_thread(
            WorkspaceEditor(source_guard).snapshot, paths
        )
        target_editor = WorkspaceEditor(WorkspacePathGuard(target_root))
        await asyncio.to_thread(target_editor.restore, snapshot)
```

Move workspace-scoped construction behind one factory taking `worktree_root`: Guard, IgnoreRules, WorkspaceFiles, GitWorkspace, RepoIndex, Context Builder, Dispatcher, WindowsLocalRuntime, Verification, and child engines must all receive that root. Provider/session/plugin registries remain application-scoped.

At startup call `recover_pending()` before enabling TUI input. At task creation prepare the worktree before creating the TaskContract, then store the managed root in `TaskAuthorization`.

- [ ] **Step 4: Wire checkpoint controls and cache invalidation**

Construct `CheckpointService`, `RewindCoordinator`, and `CheckpointControl` from the managed task services. A successful restore calls the existing inventory/repo-index invalidator with `()` and emits an Evidence invalidation observation before the task resumes.

- [ ] **Step 5: Run root tests and commit integration**

Run: `uv run --with regex python -m unittest tests.test_agent_app tests.test_command_integration tests.test_subagent_integration -v`

Expected: all selected integration tests pass.

```powershell
git add -- code_agent_win/workspace_runtime.py code_agent_win/app.py code_agent_win/host_composition.py code_agent_win/application_context.py code_agent_win/app_ui.py tests/test_agent_app.py tests/test_command_integration.py tests/test_subagent_integration.py
git commit -m "集成任务 Worktree 与 Rewind" -m "- 变更内容：所有工作区服务绑定任务 root，并接通 checkpoint、恢复和启动对账。" -m "- 变更原因：让代码恢复进入真实应用链且保护 source worktree。" -m "- 验证情况：根级任务、命令与子 Agent 集成测试通过。"
```

### Task 9: Full regression and Windows/Git fault acceptance

**Files:**
- Modify: `README.md`
- Modify only for scoped corrections: files from Tasks 2–8.

**Interfaces:**
- Consumes: completed Feature and integration tasks.
- Produces: verified Rewind delivery and accurate safety documentation.

- [ ] **Step 1: Update README behavior and limitations**

Replace the statement that the release has no worktrees with exact behavior:

```markdown
Foreground coding tasks run in managed Git worktrees. Durable checkpoints can
restore tracked and eligible untracked code, session/task state, or both.
Ignored files, secrets, build outputs, Git metadata, links/reparse targets, and
in-flight commands are never captured or replayed. Rewind keeps the original
task history and requires an explicit preview confirmation.
```

Retain the statement that no daemon, automatic push, or OS sandbox exists.

- [ ] **Step 2: Run every Feature suite**

Run: `uv run --with regex python -m unittest discover -s src/code_agent -p 'test_*.py' -v`

Expected: all tests pass.

- [ ] **Step 3: Run every root integration test**

Run: `uv run --with regex python -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all tests pass.

- [ ] **Step 4: Run syntax, packaging, and diff validation**

Run: `uv run python -m compileall -q src code_agent_win`

Expected: exit code 0.

Run: `uv run python -m build`

Expected: wheel and sdist build successfully.

Run: `git diff --check`

Expected: exit code 0 for all batch files.

- [ ] **Step 5: Perform fault-injection acceptance**

Exercise this exact matrix on Windows:

```text
dirty source: staged + unstaged + untracked → task sees eligible bytes; source bytes/index unchanged
checkpoint: edit + delete + create → code-only restores exact digest
session-only: code unchanged → replacement task sees only pre-checkpoint conversation
combined: code digest and conversation cursor both rewind → old task is superseded
locked file during restore → automatic pre-rewind rollback
kill process after pending intent → restart rolls back and never replays command
corrupt blob → preview/restore fails before file mutation
rollback failure → lineage becomes recovery_required and all writes/commands are blocked
```

- [ ] **Step 6: Commit documentation and verified corrections**

```powershell
git add -- README.md
git commit -m "记录 Worktree Rewind 使用边界" -m "- 变更内容：说明三种恢复模式、快照排除项和崩溃行为。" -m "- 变更原因：让用户准确理解代码恢复与剩余安全限制。" -m "- 验证情况：完整回归、构建与 Windows/Git 故障验收通过。"
```
