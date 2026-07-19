# Trustworthy Rewind Windows Integration Implementation Plan

> **Status:** worker-executable child plan for the Integration stage.
>
> **Scope:** root integration only. `src/` is frozen at the completed
> Sessions, Workspace, Core, and Interfaces contracts.

**Goal:** Assemble the already-completed rewind Feature contracts into the
Windows application so that every workspace mutation is either durably
captured or durably invalidates rewind coverage, checkpoints are totally
ordered with mutations, and `/rewind` exposes a read-only, two-observation
preview.

**Architecture:** One application owns one base `RewindSessionRepository`, one
`WorkspaceSnapshotStore`, one cross-process `WorkspaceMutationGate`, one
`RewindCaptureCoordinator`, one coordinated Sessions façade, and one
`RewindRuntime`. Engines, foreground control, and context compaction receive a
coordinated façade; capture and preview use the base repository. Child actions
inherit immutable root lineage. The TUI subclass delegates only to the
Interfaces read-only handler.

**Stage boundary:** Create or modify only `code_agent_win/`, root `tests/`,
root documentation/configuration, and this plan. Every command must prove:

```powershell
git diff --name-only -- src
```

prints nothing.

**Python:** resolve once per shell:

```powershell
$python = ".venv\Scripts\python.exe"
```

## Decisions that replace stale roadmap pseudocode

1. The application must instantiate `RewindSessionRepository`, not
   `SQLiteSessionRepository`.
2. `CoordinatedSessionRepository` stores the workspace fingerprint and calls
   `ensure_rewind_coverage()` inside the gate for each checkpoint. It must not
   retain a possibly stale `CoverageToken`.
3. Frozen Sessions snapshot handles are thawed in `code_agent_win` to an exact
   `dict` with a `list` `paths` value before `SnapshotHandle.from_dict()`.
4. Legacy checkpoints (`checkpoint_fact is None`) use a second
   `observe_rewind()` and compare only conversation/checkpoint facts. They do
   not compare current workspace coverage heads.
5. Windows path identity comparisons use `os.path.normcase`; display preserves
   the earliest canonical spelling.
6. A durable gap has higher precedence than a prepared mutation:
   `code-journal-incomplete` precedes `pending-workspace-mutation`.
7. Runtime validates facts; Interfaces owns the 20-row rendering bound.
8. `ModeAwareWindowsTerminalApp` uses
   `current_thread_id or state.thread_id` for `/rewind`.
9. Child lineage remains in integration. Do not add fields to
   `ChildRunRequest` or change any `src/` protocol.
10. All Python files are at most 300 lines and all functions/tests at most 50
    lines. Existing root violations are fixed before rewind behavior is added.
11. Every Sessions call whose commit order is protected by the mutation gate
    runs in a dedicated task under `asyncio.shield()`. If its caller is
    cancelled, the integration layer waits for that task to settle before
    releasing the gate, then re-raises cancellation.
12. A limited observation uses the existing read-only
    `get_rewind_checkpoint_anchor()` call as a quiescence probe. This preserves
    the fixed GAP -> PREPARED -> preview-limit reason order without changing
    the frozen Sessions Feature.

## Stable product-state layout

```text
%LOCALAPPDATA%\chaos-agent\sessions.sqlite3
%LOCALAPPDATA%\chaos-agent\rewind-snapshots\
%LOCALAPPDATA%\chaos-agent\rewind\<workspace-fingerprint>\mutation-gate.sqlite3
```

Snapshot artifacts must remain outside the workspace and outside `.git`.

---

## Task 0: Restore the integration protocol and structural baseline

**Purpose:** make the current Core/Integration boundary green before adding
rewind behavior. This is a behavior-preserving prerequisite except for fixing
the already-landed `execution_context` protocol break.

**Files:**

- Create: `code_agent_win/app_factory.py`
- Create: `code_agent_win/app_models.py`
- Create: `code_agent_win/subagent_runner.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `tests/test_app_dispatcher.py`
- Modify: `tests/test_subagent_integration.py`
- Modify: `tests/test_agent_app.py`
- Create: `tests/test_agent_app_full_stack.py`

**Forbidden:** no rewind gate/capture/runtime behavior; no `src/` changes.

### Step 0.1: Freeze the failing baseline

Add tests proving these signatures accept and forward the Core context:

```python
async def RootActionDispatcher.dispatch(
    request,
    cancellation,
    task_authorization=None,
    *,
    execution_context=None,
) -> ActionResult

async def RestrictedDispatcher.dispatch(
    request,
    cancellation,
    task_authorization=None,
    *,
    execution_context=None,
) -> ActionResult
```

Required test names:

- `test_root_dispatcher_accepts_core_execution_context`
- `test_restricted_dispatcher_forwards_execution_context_by_keyword`
- `test_read_only_dispatch_still_accepts_none_context`

Run:

```powershell
& $python -m unittest `
  tests.test_app_dispatcher `
  tests.test_subagent_integration -v
```

Expected RED: unexpected keyword `execution_context` or a fake records no
context.

Also run the three known failing full-stack tests:

```powershell
& $python -m unittest `
  tests.test_agent_app.FullStackTests.test_fake_model_read_action_round_trips_through_session `
  tests.test_agent_app.FullStackTests.test_foreground_task_repairs_a_failed_test_then_checkpoints_completion `
  tests.test_agent_app.FullStackTests.test_current_verification_evidence_expires_after_a_later_write -v
```

Expected RED: actions become `TypeError` tool failures.

### Step 0.2: Repair dispatch protocol without capture

`RootActionDispatcher.dispatch` accepts the keyword-only context and passes it
only to integration units that understand it. When capture is not configured,
read/write behavior stays byte-for-byte compatible.

`RestrictedDispatcher.dispatch` must call:

```python
return await self._inner.dispatch(
    request,
    cancellation,
    task_authorization,
    execution_context=execution_context,
)
```

Do not silently retry the old signature.

### Step 0.3: Split existing oversized integration units

- `app_models.py` owns the `Application` dataclass.
- `app_factory.py` owns small composition helpers and the current engine
  factory. `app.py` keeps public `create_application`, `_session_path`, the
  patchable `_model_client`, and compatibility re-exports.
- `subagent_runner.py` owns `EngineChildRunner`; `subagents.py` re-exports it.
- Move `FullStackTests`, `FakeModel`, and their helpers into
  `test_agent_app_full_stack.py`; keep construction/CLI/state-path tests in
  `test_agent_app.py`.
- Split dispatcher authorization and execution branches into private helpers;
  preserve returned `ActionResult` values.

Tests that patch `code_agent_win.app._model_client`,
`code_agent_win.app._session_path`, and
`code_agent_win.app.load_runtime_config` must continue to work.

### Step 0.4: Verify and commit

```powershell
& $python -m unittest `
  tests.test_agent_app `
  tests.test_agent_app_full_stack `
  tests.test_app_dispatcher `
  tests.test_subagent_integration -v
& $python -m unittest discover -s tests -p 'test_*.py' -v
& $python -m compileall -q code_agent_win tests
git diff --check
git diff --name-only -- src
```

After the split, also run the three regressions by their new fully-qualified
names under `tests.test_agent_app_full_stack.FullStackTests`:

- `test_fake_model_read_action_round_trips_through_session`
- `test_foreground_task_repairs_a_failed_test_then_checkpoints_completion`
- `test_current_verification_evidence_expires_after_a_later_write`

Run the AST structure gate across all `code_agent_win/*.py` and
`tests/test_*.py`.

Commit:

```text
整理集成结构：恢复执行上下文协议与文件粒度
```

---

## Task 1: Add the independent cross-process mutation gate

**Files:**

- Create: `code_agent_win/rewind_gate.py`
- Create: `tests/test_rewind_gate.py`

### Step 1.1: Write the complete RED suite

Required tests:

- `test_two_gate_instances_are_mutually_exclusive`
- `test_subprocess_holder_causes_bounded_timeout`
- `test_same_instance_holder_causes_bounded_timeout`
- `test_release_allows_next_holder`
- `test_crashed_holder_is_released_by_sqlite`
- `test_different_fingerprints_do_not_block`
- `test_release_may_run_on_a_different_worker_thread`
- `test_failed_acquire_releases_the_in_process_lock`
- `test_cancelled_acquire_settles_worker_and_leaks_no_lock`
- `test_concurrent_and_double_release_are_idempotent`
- `test_invalid_fingerprint_and_timeout_fail_closed`

```powershell
& $python -m unittest tests.test_rewind_gate -v
```

Expected RED: `code_agent_win.rewind_gate` is missing.

### Step 1.2: Implement the gate

Public API:

```python
class WorkspaceGateTimeout(TimeoutError):
    pass


class WorkspaceMutationGate:
    def __init__(self, product_state_root: Path, workspace_fingerprint: str)

    async def acquire(
        self,
        *,
        timeout_s: float = 5.0,
    ) -> WorkspaceGateLease


class WorkspaceGateLease:
    async def release(self) -> None
```

The database path is:

```python
product_state_root / "rewind" / fingerprint / "mutation-gate.sqlite3"
```

Validate before using either value in filesystem or SQLite operations:

- fingerprint is exactly a 64-character lowercase SHA-256 value; construct a
  `CoverageToken(fingerprint, 1)` and use its validated fingerprint;
- `timeout_s` is a finite, strictly positive real value and is not `bool`;
  reject NaN, infinity, zero, and negative values.

Acquire order:

1. compute one monotonic deadline from `loop.time() + timeout_s`;
2. acquire the instance `asyncio.Lock` with only the remaining deadline using
   `asyncio.wait_for`; timeout here maps to `WorkspaceGateTimeout`;
3. recompute the remaining duration and, in one `asyncio.to_thread` callback,
   create the parent directory and open an
   independent connection:

   ```python
   sqlite3.connect(
       path,
       timeout=remaining_s,
       isolation_level=None,
       check_same_thread=False,
   )
   ```

4. set `PRAGMA busy_timeout` from that same remaining deadline;
5. execute `BEGIN IMMEDIATE`;
6. map only in-process deadline expiry and SQLite lock/busy timeout to
   `WorkspaceGateTimeout`;
7. close partial connections and release the in-process lock on every failure.

The worker that opens the connection and executes `BEGIN IMMEDIATE` runs as a
dedicated shielded task. If acquire is cancelled, wait for that worker to
settle; if it acquired a transaction, `ROLLBACK` and close it; release the
instance lock; only then re-raise `CancelledError`. A late worker must never
leak a connection or lock.

The lease owns the connection. A per-lease async lock and `_released` flag
serialize release. `ROLLBACK` and `close` run together in one worker callback;
the in-process lock is released exactly once even if rollback reports an
error. Release also shields and settles that worker before relinquishing the
in-process lock, even if its caller is cancelled. Never reuse the Sessions
database.

### Step 1.3: Verify and commit

```powershell
& $python -m unittest tests.test_rewind_gate -v
& $python -m compileall -q code_agent_win/rewind_gate.py tests/test_rewind_gate.py
git diff --check
git diff --name-only -- src
```

Commit:

```text
新增工作区门闩：跨进程序列化回溯写入
```

---

## Task 2: Capture known edits and durable unknown-writer gaps

**Files:**

- Create: `code_agent_win/rewind_capture.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Create: `tests/test_rewind_capture.py`
- Modify: `tests/test_app_dispatcher.py`
- Create: `tests/test_rewind_plugin_integration.py`

### Step 2.1: Write capture RED tests

The successful known-edit call order is exactly:

```text
gate.acquire
sessions.ensure
workspace.prepare
snapshot.save
sessions.prepare
editor.apply
workspace.observe
sessions.complete
gate.release
cache.invalidate
```

Required tests:

- `test_known_edit_has_exact_durable_order`
- `test_snapshot_or_prepare_failure_causes_zero_file_write`
- `test_apply_failure_aborts_only_when_current_state_is_preimage`
- `test_postimage_after_apply_error_completes_but_returns_error`
- `test_third_state_after_apply_error_remains_prepared`
- `test_completion_failure_leaves_prepared_and_returns_error`
- `test_async_cancellation_waits_for_reconciliation_and_release`
- `test_cancelled_ensure_settles_before_gate_release`
- `test_cancelled_prepare_settles_before_gate_release`
- `test_cancelled_gap_settles_before_gate_release`
- `test_token_cancellation_after_write_preserves_completed_journal`
- `test_cache_invalidates_only_after_completed_journal`
- `test_invalidated_workspace_continues_typed_edit_as_gap`
- `test_gap_failure_prevents_unknown_writer_call`
- `test_external_full_local_write_never_enters_snapshot`
- `test_capture_path_requires_execution_context`
- `test_capture_rejects_mismatched_request_identity_before_side_effects`
- `test_plugin_alias_uses_translated_typed_target`
- `test_write_and_critical_mcp_record_gap_before_call`
- `test_write_and_critical_untyped_plugin_record_gap_before_call`
- `test_read_only_mcp_and_plugin_create_no_gap`
- `test_delegate_parent_creates_no_mutation_or_gap`

Run:

```powershell
& $python -m unittest `
  tests.test_rewind_capture `
  tests.test_rewind_plugin_integration `
  tests.test_app_dispatcher -v
```

Expected RED: coordinator is missing and dispatcher never records capture.

### Step 2.2: Implement `RewindCaptureCoordinator`

Public API:

```python
class RewindCaptureCoordinator:
    def __init__(
        self,
        sessions: RewindSessionRepository,
        editor: WorkspaceEditor,
        snapshots: WorkspaceSnapshotStore,
        gate: WorkspaceMutationGate,
        *,
        existing_baseline: RewindBaseline = RewindBaseline.UNKNOWN,
    ) -> None

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

Both methods first require exact types (`type(context) is
ActionExecutionContext`, `type(request) is ActionRequest`) and
`context.request_id == request.id`. A mismatch fails closed before gate,
snapshot, journal, file, cache, provider, or tool I/O. Then they copy:

| Sessions field | Core field |
|---|---|
| owner_thread_id | context.owner_thread_id |
| origin_thread_id | context.origin_thread_id |
| task_id | context.task_id |
| parent_request_id | context.parent_request_id |
| request_id | context.request_id |
| action_name | translated request.name |

Known active coverage:

1. acquire gate;
2. `ensure_rewind_coverage(fingerprint)`;
3. `prepare_edit_state` in a worker;
4. save the exact inverse snapshot in a worker;
5. create one `RewindMutationPath`;
6. `prepare_rewind_mutation`;
7. run `WorkspaceEditor.apply` in a worker;
8. re-observe and require exact expected postimage;
9. complete the mutation;
10. release in `finally`.

Call `SnapshotHandle.to_dict()` before Sessions prepare.

All ordered Sessions operations inside the lease
(`ensure_rewind_coverage`, prepare, complete, abort, and gap) use a shared
integration helper that creates a task, awaits it through `asyncio.shield()`,
and, on caller cancellation, waits for the task to settle before control may
reach gate release. Reconciliation may then issue another shielded ordered
Sessions call. Tests block the underlying database worker and assert that a
competing lease cannot enter until the commit/rollback result is known.

The helper preserves both the settled result/error and the original
cancellation instead of immediately discarding a successful result. In the
special case where cancellation arrives during
`prepare_rewind_mutation` and the worker subsequently commits, `apply_edit`
must:

1. retain the returned mutation ID;
2. perform zero file writes;
3. observe and require the unchanged preimage;
4. shield and settle `abort_rewind_mutation(mutation_id)`;
5. release the gate only after the row is `ABORTED`;
6. re-raise the original cancellation.

If the prepare worker fails without committing, there is no mutation ID to
abort; release after it settles and re-raise the original cancellation.
`test_cancelled_prepare_settles_before_gate_release` asserts the final
`ABORTED` row, zero file writes, and that a competing lease enters only after
the abort settles.

Baseline:

- missing preimage: `ABSENT`;
- existing in a non-Git application: `NON_GIT_EXISTING`;
- otherwise: `UNKNOWN`.

Classification never substitutes for byte/hash validation.

If coverage is already `INVALIDATED`, `apply_edit` directly calls
`sessions.record_rewind_gap()` with reason `coverage-already-invalidated`
using the current coverage token, then executes the approved typed edit while
still holding that same lease. It must not call the public `record_gap()`,
which would recursively acquire the non-reentrant gate. The exact branch is:

```text
gate.acquire -> sessions.ensure -> sessions.gap -> editor.apply
-> gate.release
```

It creates no snapshot or prepared mutation and never pretends coverage
became active.

If apply or task cancellation races a worker, shield and await the worker,
then reconcile:

- current == preimage: abort prepared row;
- current == postimage: complete prepared row;
- any third state: leave prepared.

After reconciliation, re-raise the original failure/cancellation. Completion
failure leaves `PREPARED`.

`record_gap` acquires the same gate, lazily ensures coverage, writes one
`RewindGapPrepare` through the shield-and-settle helper, then releases. First
unknown writer reason is `unknown-writer`.

### Step 2.3: Integrate after policy and approval

`RootActionDispatcher` receives optional `capture`. Production will always
inject it; legacy unit fixtures may leave it `None`.

After translation, validation, policy, and approval:

- workspace-relative `write_file`/`replace_text`: coordinator `apply_edit`;
- allowed absolute FULL_LOCAL external path: execute out of rewind scope and
  never pass its absolute path to snapshot/Sessions;
- `run_command`, `run_verification`: durable gap before call;
- MCP risk `write` or `critical`: durable gap before call;
- plugin risk `write` or `critical` when not translated to a typed known edit:
  durable gap before call;
- read-only: no row;
- `delegate_agent`: no parent row.

Any capture/gap path requires non-null `execution_context`. Call
`cancellation.raise_if_cancelled()` before capture and after a completed known
write. Re-raise `CancellationError` and `asyncio.CancelledError`; do not turn
them into ordinary tool errors.

Invalidate cache only after coordinator success.

The current production factory intentionally keeps
`WorkspacePathGuard(root, allow_outside=False)` in every mode. Add a
production-composition test proving an absolute external path is rejected
before capture. Keep the permissive FULL_LOCAL bypass case as a dispatcher
unit test built with an explicit `allow_outside=True` guard; do not weaken the
production guard or claim that branch is currently reachable.

### Step 2.4: Verify and commit

```powershell
& $python -m unittest `
  tests.test_rewind_capture `
  tests.test_rewind_plugin_integration `
  tests.test_app_dispatcher `
  tests.test_agent_app_full_stack -v
& $python -m compileall -q code_agent_win tests
git diff --check
git diff --name-only -- src
```

Commit:

```text
捕获工作区变更：持久化已知写入与覆盖缺口
```

---

## Task 3: Totally order checkpoints and preserve child action lineage

**Files:**

- Create: `code_agent_win/rewind_sessions.py`
- Modify: `code_agent_win/action_dispatcher.py`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/subagent_runner.py`
- Modify: `code_agent_win/subagents.py`
- Modify: `code_agent_win/app_factory.py`
- Modify: `tests/test_agent_app.py`
- Create: `tests/test_rewind_checkpoint_ordering.py`
- Create: `tests/test_rewind_lineage_integration.py`
- Modify: `tests/test_subagent_integration.py`
- Modify: `tests/test_context_runtime.py`

### Step 3.1: Write checkpoint ordering RED tests

Required tests:

- `test_checkpoint_commits_before_mutation`
- `test_checkpoint_commits_after_completed_mutation`
- `test_checkpoint_cannot_commit_old_high_water_after_file_write`
- `test_prepared_mutation_blocks_checkpoint`
- `test_cancelled_checkpoint_commit_settles_before_gate_release`
- `test_gate_or_anchor_failure_never_falls_back_unanchored`
- `test_child_facade_anchors_checkpoint_to_root_owner`
- `test_for_owner_rejects_blank_owner`
- `test_forwarded_repository_methods_preserve_legacy_behavior`

Only these total orders are valid:

```text
checkpoint commit -> mutation prepare -> file write -> mutation complete
mutation prepare -> file write -> mutation complete -> checkpoint commit
```

### Step 3.2: Implement `CoordinatedSessionRepository`

```python
class CoordinatedSessionRepository:
    def __init__(
        self,
        base: RewindSessionRepository,
        gate: WorkspaceMutationGate,
        workspace_fingerprint: str,
        *,
        fixed_owner_thread_id: str | None = None,
    ) -> None

    def __getattr__(self, name: str) -> object

    def for_owner(
        self,
        owner_thread_id: str,
    ) -> CoordinatedSessionRepository

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> str
```

Inside one gate lease:

1. `ensure_rewind_coverage(workspace_fingerprint)`;
2. select `fixed_owner_thread_id or thread_id`;
3. `get_rewind_checkpoint_anchor(record.token, owner)`;
4. base `create_checkpoint(..., rewind_anchor=anchor)`;
5. release in `finally`.

The ensure, anchor, and create calls each use the same shield-and-settle helper
as capture. Cancellation during any database worker must not reach
`gate.release` until that operation has committed or failed. In particular,
the cancelled-checkpoint test blocks `create_checkpoint`, requests
cancellation, proves a mutation lease cannot enter, then permits the database
operation to settle and checks the resulting total order.

Gate/coverage/anchor/create failure propagates. Never retry as an unanchored
checkpoint.

### Step 3.3: Write lineage RED tests

The restricted-dispatcher context test is a Task 0 regression and must already
be GREEN. The actual lineage RED tests are:

- `test_child_write_uses_root_owner_child_origin_task_and_parent_request`
- `test_two_concurrent_children_keep_distinct_parent_request_ids`
- `test_writable_child_without_lineage_fails_before_dispatch`
- `test_read_only_child_without_lineage_can_run`
- `test_delegate_parent_does_not_record_duplicate_mutation`
- `test_child_engine_and_compactor_use_root_scoped_repository`

### Step 3.4: Preserve lineage without changing `src/`

`RootActionDispatcher._execute(..., execution_context=...)` forwards the
parent `ActionExecutionContext` to
`SubagentRuntime.dispatch(..., execution_context=execution_context)`, but does
not journal the delegate itself. This change belongs to Task 3 and is covered
by `code_agent_win/action_dispatcher.py` in the allowlist.

`EngineChildRunner` owns a task-local `ContextVar` and exposes token/reset
helpers. `SubagentRuntime.dispatch` binds the parent context around the awaited
supervisor call and resets it in `finally`. Concurrent dispatch tests must
prove isolation.

The child factory receives the bound parent context and constructs:

```python
ActionLineage(
    owner_thread_id=parent.owner_thread_id,
    task_id=parent.task_id,
    parent_request_id=parent.request_id,
)
```

Core then supplies:

- owner = root owner;
- origin = actual child thread;
- parent request = delegate request;
- task = parent task.

A child with `may_write=True` and no bound parent context returns a structured
error before its engine/dispatcher runs. Read-only roles may run without
lineage.

Main engine, foreground controller, verification service, and main context use
the coordinated repository. Child engine/context use
`coordinated.for_owner(parent.owner_thread_id)`. Capture/runtime keep the base
repository.

Task 3 also establishes the production write-side composition:

- add patchable `_product_state_root()` in `code_agent_win.app`, defaulting to
  `%LOCALAPPDATA%\chaos-agent`, and forward that exact `Path` into
  `app_factory`;
- re-export it from `app.py` so existing patch-based tests can isolate state;
- construct
  `WorkspaceSnapshotStore(guard, product_state_root / "rewind-snapshots")`;
- construct `WorkspaceMutationGate(product_state_root, fingerprint)`;
- construct the base `RewindSessionRepository`, capture coordinator, and
  coordinated façade once;
- pass `existing_baseline=NON_GIT_EXISTING` exactly when `git is None`, and
  `UNKNOWN` otherwise;
- inject capture into the dispatcher and the coordinated façade into main,
  foreground, verification, context, and child owners.

Application tests patch `_product_state_root()` to a sibling directory outside
the workspace and assert the snapshot and gate paths match the stable layout.
Task 5 reuses this graph and adds only runtime/TUI read-side wiring.

### Step 3.5: Verify and commit

```powershell
& $python -m unittest `
  tests.test_rewind_checkpoint_ordering `
  tests.test_rewind_lineage_integration `
  tests.test_subagent_integration `
  tests.test_context_runtime `
  tests.test_agent_app_full_stack -v
& $python -m compileall -q code_agent_win tests
git diff --check
git diff --name-only -- src
```

Commit:

```text
接入回溯顺序：协调检查点并保留子动作谱系
```

---

## Task 4: Validate observations in the read-only runtime

**Files:**

- Create: `code_agent_win/rewind_runtime.py`
- Create: `code_agent_win/_rewind_runtime_projection.py`
- Create: `code_agent_win/_rewind_runtime_validation.py`
- Create: `tests/test_rewind_runtime.py`
- Create: `tests/test_rewind_runtime_integrity.py`
- Create: `tests/test_rewind_runtime_asof.py`

### Step 4.1: Write the complete runtime RED suite

`test_rewind_runtime.py`:

- candidate fields/cursor/limit map unchanged;
- legacy checkpoint can preview conversation only;
- missing/invalid conversation bounds map exactly;
- cross-thread checkpoint maps to not found;
- zero-message conversation is enabled;
- zero-mutation code is enabled;
- code-only ignores a message-bound failure;
- child checkpoint selects root-owned effects;
- earliest owned baseline is projected.

`test_rewind_runtime_integrity.py`:

- old/middle same-path reverse chain;
- real frozen Sessions handle thaw;
- missing manifest/blob;
- corrupt/foreign manifest;
- malformed handle;
- snapshot/path/preimage mismatch;
- GAP, PREPARED, and GAP+PREPARED precedence;
- GAP+limit and PREPARED+limit precedence using the real Sessions repository;
- limited PREPARED completing/aborting between the two quiescence probes;
- foreign disjoint and overlap;
- Windows case-alias overlap;
- same-path discontinuity;
- current-tip user edit;
- mutation/path limit exposes no partial paths;
- current-state byte overflow.

`test_rewind_runtime_asof.py`:

- head/path first move then stable second attempt;
- two moves become source-changed;
- legacy conversation compares no workspace heads;
- conversation-only performs no snapshot load;
- stable as-of fields;
- exact `RewindFacts` field mapping for success and each disabled facet;
- disabled code has no digest and zero mutation uses the empty-state digest;
- not-found and source-changed mirror one global reason into both facets;
- source-changed fallback has no claimed observations;
- Sessions corruption, cancellation, and unexpected I/O propagate;
- preview performs no write/provider/action/approval/Git/apply.

```powershell
& $python -m unittest `
  tests.test_rewind_runtime `
  tests.test_rewind_runtime_integrity `
  tests.test_rewind_runtime_asof -v
```

Expected RED: `code_agent_win.rewind_runtime` is missing.

### Step 4.2: Implement public runtime API

```python
class RewindRuntime:
    def __init__(
        self,
        sessions: object,
        snapshots: WorkspaceSnapshotStore,
        editor: WorkspaceEditor,
        *,
        limits: RewindReadLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None

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

No apply/restore/confirm method exists.

Candidate mapping is one-to-one and the opaque cursor is unchanged.

### Step 4.3: Implement conversation and code projection

Conversation:

```python
def _conversation_projection(observation):
    bound = observation.checkpoint.message_sequence
    if bound is None:
        return 0, MESSAGE_BOUND_MISSING
    count = observation.conversation_message_count
    if count is None or bound > observation.heads.message_sequence:
        return 0, MESSAGE_BOUND_INVALID
    return count, None
```

Event bounds never disable conversation.

Code precheck, in stable reason priority:

1. no checkpoint fact -> code-coverage-unavailable;
2. checkpoint or current head coverage is `INVALIDATED`, or any visible GAP
   exists -> code-journal-incomplete;
3. any visible PREPARED exists -> pending-workspace-mutation;
4. when `limit_exceeded` hides rows, construct
   `CoverageToken(fact.workspace_fingerprint, heads.coverage_generation)` and
   call the public read-only
   `get_rewind_checkpoint_anchor(token, fact.owner_thread_id)` as the first
   quiescence probe;
5. make the attempt's second observation and compare validated
   checkpoint/full heads;
6. after identical heads, repeat the same quiescence probe: if it succeeds,
   no PREPARED row exists at the final as-of boundary and the reason is
   preview-limit-exceeded; if both probes raise `SessionStorageError`, the
   stable non-quiescent condition maps to pending-workspace-mutation;
7. any moved token/head restarts the complete attempt.

`SessionNotFound`, `SessionCorruptionError`, unexpected storage/I/O errors, and
cancellation from either observation or the probe propagate unchanged.
Never inspect private Sessions state or exception text. The real-repository
GAP+limit, PREPARED+limit, and PREPARED-complete/abort-between-probes tests lock
this behavior to the frozen public API.

Then:

1. require checkpoint fingerprint equals current snapshot-store fingerprint;
2. choose completed mutations owned by
   `checkpoint_fact.owner_thread_id`;
3. use `os.path.normcase` for path identity;
4. reject completed foreign-owner overlap;
5. thaw each frozen handle to exact dict/list;
6. load one inverse snapshot per owned mutation in a worker;
7. require snapshot `(path, existed, sha256)` equals persisted preimage;
8. require adjacent owned facts on each path are continuous;
9. observe current states and require latest owned postimages;
10. project earliest owned baseline and stable sorted paths;
11. compute `relevant_path_digest(first_states)`.

For every projected path:

```text
baseline_provenance = earliest RewindMutationPath.baseline.value
preserves_pre_agent_baseline = True
```

The boolean is true only after the exact earliest snapshot, continuity, and
current-tip validations in this section all pass. `RewindBaseline` is display
provenance, not proof strength; therefore an `UNKNOWN` provenance still
preserves the verified pre-agent bytes and remains `True`.

Mappings:

| Failure | Interface reason |
|---|---|
| missing artifact | snapshot-missing |
| handle/integrity/preimage mismatch | snapshot-invalid |
| foreign overlap/continuity/current tip | workspace-conflict |
| state byte limit | preview-limit-exceeded |

Unexpected Sessions corruption/storage/I/O propagates.

Zero owned mutations is enabled and uses `relevant_path_digest(())`.
Every ordinary result is first constructed as `RewindFacts` and then passed to
`build_rewind_preview(kind, facts)`; runtime never directly assembles a
`RewindPreview`.

Construct ordinary facts with this exact table:

| `RewindFacts` / `RewindAsOf` field | Source |
|---|---|
| checkpoint_id/label/created_at | `observation.checkpoint.id/label/created_at` |
| message_sequence | final stable `observation.heads.message_sequence` |
| event_sequence | final stable `observation.heads.event_sequence` |
| mutation_sequence | final stable head only when code is selected and the checkpoint fact matches the current workspace; otherwise `None` |
| coverage_generation | final stable head under the same condition; otherwise `None` |
| relevant_path_digest | digest of the first exact current-state observation only after successful code projection; `None` for conversation-only or any disabled code facet |
| captured_at | injected UTC clock called after the successful attempt's final observation/state comparison |
| conversation_messages | validated count, or `0` when its facet is disabled |
| code_paths | complete sorted projection, or `()` when its facet is disabled |
| conversation_disabled_reason | validated conversation reason or `None` |
| code_disabled_reason | validated code reason or `None` |

For successful zero-mutation code, the digest is exactly
`relevant_path_digest(())`, not `None`.

### Step 4.4: Implement facet-aware two-observation stability

Perform at most two complete attempts.

- conversation: compare checkpoint identity, message/event heads, and count;
- code/both with a current-workspace checkpoint fact: compare full heads;
- successful code projection: observe the same paths again and compare exact
  states;
- legacy checkpoint: second `observe_rewind`, never compare current workspace
  coverage heads.

If attempt one moved, repeat from the beginning. If attempt two moved, return a
global `source-changed-during-preview` with empty messages/paths and all as-of
observation fields `None`.

Global fallbacks are also created through `RewindFacts` then
`build_rewind_preview`. Because Interfaces requires global failures to disable
both facets identically, both `conversation_disabled_reason` and
`code_disabled_reason` receive the same global reason; messages are `0`, paths
are `()`, and message/event/mutation/generation/digest in `RewindAsOf` are all
`None`.

`checkpoint-not-found` returns:

- requested checkpoint ID;
- fixed label `checkpoint unavailable`;
- `checkpoint_created_at` and `as_of.captured_at` equal one local UTC fallback
  clock value required by the strict model, not a claimed source timestamp.

`source-changed-during-preview` returns:

- requested checkpoint ID;
- fixed label `checkpoint changed during preview`;
- the same empty mirrored global shape and one post-retry UTC fallback clock
  value for both required timestamps.

`RewindAsOf` mutation/generation fields are populated only when code is
selected and the checkpoint fact belongs to the current workspace.

### Step 4.5: Verify and commit

```powershell
& $python -m unittest `
  tests.test_rewind_runtime `
  tests.test_rewind_runtime_integrity `
  tests.test_rewind_runtime_asof -v
& $python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
& $python -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
& $python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
& $python -m compileall -q code_agent_win tests
git diff --check
git diff --name-only -- src
```

Commit:

```text
验证回溯观测：生成可信只读预览
```

---

## Task 5: Wire shared rewind composition and the TUI subclass

**Files:**

- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/app_factory.py`
- Modify: `code_agent_win/app_models.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `code_agent_win/AGENTS.md`
- Modify: `tests/test_agent_app.py`
- Create: `tests/test_rewind_tui_integration.py`

### Step 5.1: Write application/TUI RED tests

Required tests:

- `test_application_injects_one_shared_rewind_runtime`
- `test_application_uses_rewind_session_repository`
- `test_application_uses_coordinated_sessions_for_engine_and_foreground`
- `test_rewind_command_is_visible_only_with_runtime`
- `test_list_is_paginated_and_says_candidate_only`
- `test_conversation_code_and_both_previews_render`
- `test_disabled_reason_value_is_stable`
- `test_footer_says_preview_only_apply_unavailable_and_no_git_reset`
- `test_command_calls_no_provider_action_approval_or_checkpoint`
- `test_state_thread_id_fallback_allows_foreground_rewind`
- `test_non_rewind_commands_delegate_to_base_handler`
- `test_application_uses_keyword_construction`
- `test_snapshot_product_state_is_outside_workspace`
- `test_product_state_paths_match_snapshot_and_gate_layout`
- `test_production_guard_rejects_external_path_before_capture`

```powershell
& $python -m unittest `
  tests.test_rewind_tui_integration `
  tests.test_agent_app -v
```

Expected RED: no runtime injection and command unavailable.

### Step 5.2: Complete the one shared integration graph

Reuse the write-side objects established in Task 3; never construct a second
base repository, store, gate, capture coordinator, or coordinated façade.
Complete this construction order:

1. guard, files, editor;
2. patchable product-state root;
3. `WorkspaceSnapshotStore(guard, product_state_root / "rewind-snapshots")`;
4. base `RewindSessionRepository`;
5. `WorkspaceMutationGate(product_state_root, fingerprint)`;
6. `RewindCaptureCoordinator` with Git-aware existing baseline;
7. `CoordinatedSessionRepository`;
8. dispatcher with capture;
9. main/child engines and compactors with the correct façade;
10. `RewindRuntime` with base repository;
11. TUI with runtime.

The `_product_state_root()` helper introduced in Task 3 remains patchable from
`code_agent_win.app` tests.
Application tests must use a sibling product-state directory outside their
workspace; do not weaken Workspace safety.

`Application` adds:

```python
rewind: RewindRuntime | None = None
```

Construct it using keywords only. Existing close order stays subagents,
provider/model, then MCP; rewind has no invented close action.

### Step 5.3: Wire `ModeAwareWindowsTerminalApp`

Constructor:

```python
def __init__(
    self,
    *args: object,
    capability: ModePermissionView,
    plugin_errors: tuple[str, ...] = (),
    rewind: RewindPreviewSource | None = None,
    **kwargs: object,
) -> None
```

Store it as public `self.rewind` so `available_services()` can gate the
command.

Override `_handle_command`:

```python
command = outcome.command
assert command is not None
if command.kind is TuiCommandKind.REWIND:
    assert self.rewind is not None
    result = await handle_rewind_command(
        self.rewind,
        self.current_thread_id or self.state.thread_id,
        command.instruction,
    )
    self._append(result.display_kind, result.text)
    return result.handled
return await super()._handle_command(outcome)
```

The override preserves the base method's `outcome.command` assertion and
passes the unchanged `ParseOutcome` to all non-rewind commands.

Do not modify `src/code_agent/interfaces/windows_tui.py`.

### Step 5.4: Update integration Units

Add only the main contracts:

- `WorkspaceMutationGate`
- `RewindCaptureCoordinator`
- `CoordinatedSessionRepository`
- `RewindRuntime`
- `ModeAwareWindowsTerminalApp` rewind delegation

Document side effects and the no-apply boundary.

### Step 5.5: Verify and commit

```powershell
& $python -m unittest discover -s tests -p 'test_rewind_*.py' -v
& $python -m unittest `
  tests.test_agent_app `
  tests.test_agent_app_full_stack `
  tests.test_command_integration `
  tests.test_subagent_integration `
  tests.test_context_runtime -v
& $python -m compileall -q code_agent_win tests
git diff --check
git diff --name-only -- src
```

Commit:

```text
集成回溯预览：共享捕获依赖并接通只读 TUI
```

---

## Task 6: Reconcile documentation and run the final acceptance suite

**Files:**

- Modify: `docs/superpowers/plans/2026-07-16-cli-tui-p0-recovery-review-plan.md`
- Modify: `README.md`
- Modify: `docs/amp-inspired-runtime.md`
- Modify: `docs/research/cli-tui-design-comparison.md`
- Modify: `code_agent_win/AGENTS.md` only if final contract correction is needed

### Step 6.1: Update only verified claims

Documentation must state:

- arbitrary checkpoint rewind is preview-only;
- list/preview support conversation/code/both;
- code preview depends on complete capture coverage and validated snapshots;
- any unknown writer durably disables code rewind for that coverage;
- no apply, restore, Git reset, approval, provider, or tool action is available
  from `/rewind`;
- pre-existing user state is preserved only when exact inverse snapshot and
  continuity/current-tip validation pass;
- candidate facets are not availability promises.

Do not mark apply or destructive restore complete.

### Step 6.2: Run every Feature suite

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

### Step 6.3: Run root, compilation, structure, and boundary gates

```powershell
& $python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'failed root integration suite' }
& $python -m compileall -q src code_agent_win tests
if ($LASTEXITCODE -ne 0) { throw 'compileall failed' }
git diff --check
git diff --name-only -- src
git status --short
```

Additional mandatory probes:

- two independent gate instances and one subprocess contend correctly;
- known edit order and cancellation reconciliation;
- checkpoint/mutation barrier order;
- two concurrent children retain different parent request IDs;
- frozen handle thaw and Windows case-alias overlap;
- two-as-of retry and legacy conversation-only;
- TUI command makes no provider/action/approval/checkpoint call;
- no public runtime/capture/command method named apply, restore, reset,
  confirm, or delete;
- every Python source/test file ≤300 lines and every function/test ≤50 lines.

### Step 6.4: Review, commit, and hand off

Run an independent specification review and an independent quality/security
review over the whole Integration diff. Resolve every Important issue before
claiming completion.

Commit:

```text
完成回溯集成：核对文档并通过全量验收
```

## Definition of done

- All four Feature suites and the root suite pass.
- Worktree is clean.
- `src/` has no Integration-stage diff.
- Every captured edit is durably prepared before file mutation.
- Every unknown writer has a durable gap before execution.
- Checkpoints cannot observe a stale pre-mutation high-water.
- Child writes retain root owner and exact parent request lineage.
- Runtime performs no mutation and exposes no apply API.
- `/rewind` is visible only when the runtime is injected.
- Documentation describes preview-only behavior without overstating restore.
