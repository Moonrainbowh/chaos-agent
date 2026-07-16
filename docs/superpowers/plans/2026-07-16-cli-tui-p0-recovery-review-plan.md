# CLI/TUI P0 Recovery and Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the report's P0 runtime identity, source-anchored compaction, complete working-tree review, and safe rewind preview without changing Chaos4's native-scrollback or permission model.

**Architecture:** Core creates one immutable `ContextRequest` per model turn and passes real thread/turn identity to Context. Context may invoke an injected anchored compactor, while the Windows integration persists any derived semantic checkpoint. Workspace exposes bounded Git diff facets and durable byte snapshots; Interfaces only project those facts and never execute Git or restore files.

**Tech Stack:** Python 3.10+, `dataclasses`, `asyncio`, SQLite, fixed-argv Git subprocesses, `unittest`, Windows Terminal integration.

---

## Scope and ordering

This plan implements only P0 from `docs/research/cli-tui-design-comparison.md`:

1. deterministic baseline repair;
2. real `ContextRequest` identity and source anchors;
3. staged, unstaged, untracked, per-turn, and since-checkpoint diff projections;
4. durable workspace snapshots and code/conversation/both rewind previews;
5. Windows integration and full regression verification.

Keymap remapping, `@`/`@@` pickers, Transcript Inspector, background jobs, daemon, remote control, automatic worktrees, commit, push, and native OS sandbox are outside this plan.

## File map

| Area | Responsibility |
| --- | --- |
| `src/code_agent/core/context_request.py` | Immutable per-turn context identity, runtime snapshots, cancellation, and budget lease |
| `src/code_agent/core/protocols.py` | `ContextBuilder.build(request)` protocol |
| `src/code_agent/core/engine.py` | Construct requests from real thread and turn state |
| `src/code_agent/context/builder.py` | Build bounded context from a request and optional anchored compactor |
| `src/code_agent/thread_intelligence/deterministic_summary.py` | Deterministic bounded summarizer used without hidden provider calls |
| `code_agent_win/context_runtime.py` | Persist semantic checkpoints through Sessions and adapt mode/permission facts |
| `src/code_agent/workspace/git.py` | Bounded staged/unstaged/untracked Git facts |
| `src/code_agent/interfaces/diff_view.py` | Scope-aware immutable diff documents and navigation |
| `src/code_agent/workspace/snapshot_store.py` | Product-state snapshot persistence and integrity checks |
| `src/code_agent/sessions/_records.py` | Checkpoint message/event bounds and lookup |
| `src/code_agent/interfaces/rewind_view.py` | Side-effect-free rewind preview |
| `code_agent_win/app.py`, `code_agent_win/app_ui.py` | Integration only after Feature Units pass |

### Task 0: Isolate ambient mode bindings in the integration test

**Stage:** Bug fix

**Files:**
- Modify: `tests/test_agent_app.py`
- Test: `tests/test_agent_app.py`

- [ ] **Step 1: Preserve the observed RED evidence**

Run with ambient `CHAOS_MODE_*_PROFILE` values:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agent_app.ApplicationConstructionTests.test_tui_uses_the_session_repository_for_history -v
```

Expected: ERROR with `mode profile is not configured: low -> deepseek` or the machine's configured low-profile name.

- [ ] **Step 2: Isolate only the application-construction call**

Wrap the existing `create_application(root)` block with:

```python
with patch.dict("os.environ", {}, clear=True):
    with patch("code_agent_win.app._model_client", return_value=object()):
        with patch(
            "code_agent_win.app._session_path",
            return_value=root / "sessions.sqlite3",
        ):
            with patch("code_agent_win.app.load_runtime_config", return_value=runtime):
                application = create_application(root)
```

Do not change `build_mode_registry`: real missing profile bindings must continue to fail closed.

- [ ] **Step 3: Verify GREEN and the complete root suite**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agent_app.ApplicationConstructionTests.test_tui_uses_the_session_repository_for_history -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

Expected: both commands pass.

- [ ] **Step 4: Commit the baseline repair**

```powershell
git add tests/test_agent_app.py
git commit -m "test: isolate ambient mode bindings"
```

### Task 1: Freeze P0 Feature contracts

**Stage:** Requirements

**Files:**
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `src/code_agent/thread_intelligence/AGENTS.md`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`

- [ ] **Step 1: Add only boundary statements**

Record these contracts without adding implementation or new Unit entries:

```text
Core: one immutable ContextRequest carries real thread_id and positive revision.
Context: compaction receives identity and cancellation but never persists checkpoints.
Thread Intelligence: derived checkpoints expose stable source ranges and remain untrusted.
Workspace: snapshots live outside the repository and restore the pre-turn bytes, including a dirty baseline.
Sessions: checkpoints record message/event bounds and opaque artifact handles.
Interfaces: diff and rewind views are read-only projections; preview is distinct from apply.
```

- [ ] **Step 2: Contract review**

Verify each Feature remains at ten or fewer principal Unit groups and that no boundary grants permission, runs Git, or rewrites user history.

- [ ] **Step 3: Commit requirements**

```powershell
git add src/code_agent/core/AGENTS.md src/code_agent/context/AGENTS.md src/code_agent/thread_intelligence/AGENTS.md src/code_agent/workspace/AGENTS.md src/code_agent/interfaces/AGENTS.md src/code_agent/sessions/AGENTS.md
git commit -m "docs: define p0 recovery and review contracts"
```

### Task 2: Introduce immutable ContextRequest and real turn identity

**Stage:** Feature implementation

**Files:**
- Create: `src/code_agent/core/context_request.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/core/tests/_engine_support.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`
- Modify: `src/code_agent/core/tests/test_protocols.py`

- [ ] **Step 1: Write failing request-identity tests**

Add a recording builder and assert the request has the actual thread, current positive turn revision, same cancellation token, mode/permission snapshots, and a bounded budget lease:

```python
class RecordingContextBuilder:
    def __init__(self) -> None:
        self.requests = []

    async def build(self, request):
        self.requests.append(request)
        return ContextBundle("system", request.messages)

self.assertEqual(builder.requests[0].thread_id, thread_id)
self.assertEqual(builder.requests[0].revision, 1)
self.assertIs(builder.requests[0].cancellation, token)
self.assertEqual(builder.requests[0].budget_lease["model_turns"], 1)
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest src.code_agent.core.tests.test_engine_run src.code_agent.core.tests.test_protocols -v
```

Expected: FAIL because `ContextRequest` and the one-argument protocol do not exist.

- [ ] **Step 3: Implement the immutable request**

Create the public shape:

```python
@dataclass(frozen=True)
class ContextRequest:
    thread_id: str
    revision: int
    messages: tuple[Message, ...]
    user_input: str
    tools: tuple[ToolDefinition, ...]
    task_state: TaskState
    cancellation: CancellationToken
    mode_snapshot: Mapping[str, JSONValue] = field(default_factory=dict)
    permission_snapshot: Mapping[str, JSONValue] = field(default_factory=dict)
    context_pressure: float | None = None
    timeout_seconds: float | None = None
    budget_lease: Mapping[str, JSONValue] = field(default_factory=dict)
```

`__post_init__` must validate non-blank `thread_id`, positive non-boolean revision, tuple contents, finite `context_pressure` in `[0, 1]`, positive finite timeout, and freeze all JSON mappings.

- [ ] **Step 4: Change the protocol and engine call**

`ContextBuilder` becomes:

```python
class ContextBuilder(Protocol):
    async def build(self, request: ContextRequest) -> ContextBundle: ...
```

`AgentEngine` accepts frozen `context_mode_snapshot` and `context_permission_snapshot` mappings and constructs one request before each context build. The budget lease contains only numeric limits/usage and never Prompt or file content.

- [ ] **Step 5: Verify Core GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: all Core tests pass.

- [ ] **Step 6: Update the Unit list and commit**

Add `ContextRequest` to `src/code_agent/core/AGENTS.md`, then:

```powershell
git add src/code_agent/core
git commit -m "feat: carry real context request identity"
```

### Task 3: Build source-anchored compaction and persist checkpoint facts

**Stage:** Feature implementation, followed by integration

**Files:**
- Create: `src/code_agent/thread_intelligence/deterministic_summary.py`
- Modify: `src/code_agent/thread_intelligence/compaction.py`
- Modify: `src/code_agent/thread_intelligence/models.py`
- Modify: `src/code_agent/thread_intelligence/AGENTS.md`
- Modify: `src/code_agent/thread_intelligence/tests/test_compaction.py`
- Modify: `src/code_agent/context/builder.py`
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `src/code_agent/context/tests/test_builder.py`
- Modify: `src/code_agent/core/models.py`
- Create: `code_agent_win/context_runtime.py`
- Modify: `code_agent_win/AGENTS.md`
- Modify: `code_agent_win/app.py`
- Modify: `tests/test_agent_app.py`

- [ ] **Step 1: Write failing deterministic-summary and builder tests**

The summarizer must preserve source ordering, use no provider, obey `max_summary_tokens`, and return model `deterministic-anchor-v1`. The builder test passes a `ContextRequest(thread_id="thread-a", revision=2, ...)` and an injected recording compactor, then asserts exact identity and cancellation propagation.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/thread_intelligence/tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest src.code_agent.context.tests.test_builder -v
```

Expected: FAIL because the deterministic summary service and request-based builder do not exist.

- [ ] **Step 3: Implement the deterministic bounded summarizer**

Expose:

```python
class DeterministicSummaryService:
    async def summarize(
        self, request: SummaryRequest, cancellation: CancellationToken
    ) -> SummaryResponse:
        cancellation.raise_if_cancelled()
        summary = render_bounded_source_summary(request.sources, request.max_summary_tokens)
        return SummaryResponse(summary, "deterministic-anchor-v1", Usage())


def render_bounded_source_summary(
    sources: tuple[AnchoredMessage, ...], max_tokens: int
) -> str:
    lines = ["Untrusted conversation checkpoint:"]
    for source in sources:
        message = source.message
        actions = ",".join(call.name for call in message.tool_calls)
        label = message.role + (f" action={actions}" if actions else "")
        snippet = " ".join(message.content.split())
        lines.append(f"- {label}: {truncate_to_tokens(snippet, 12)}")
    return truncate_to_tokens("\n".join(lines), max_tokens)
```

The renderer includes role, action name, and a bounded text snippet; it never includes raw hidden reasoning.

Add stable payload conversion in `thread_intelligence/models.py`:

```python
def semantic_checkpoint_payload(checkpoint: SemanticCheckpoint) -> dict[str, JSONValue]:
    return {
        "id": checkpoint.id,
        "thread_id": checkpoint.thread_id,
        "source_start": checkpoint.source_start.to_dict(),
        "source_end": checkpoint.source_end.to_dict(),
        "source_digest": checkpoint.source_digest,
        "model": checkpoint.model,
        "usage": checkpoint.usage.to_dict(),
        "version": checkpoint.version,
    }
```

`SourceAnchor.to_dict()` returns only kind, sequence, stable ID, digest, and thread ID. The persisted payload deliberately omits `checkpoint.summary` and the original message text.

- [ ] **Step 4: Make WorkspaceContextBuilder request-based**

`WorkspaceContextBuilder.build(request)` validates the request, computes the message allocation, invokes an optional structural anchored compactor with the real thread ID, and always applies the existing deterministic compactor as the final hard bound. Add supported numeric measurements `semantic_triggered`, `semantic_fallback`, and `semantic_source_count` to `ContextBundle`.

- [ ] **Step 5: Persist checkpoints in integration, not Context**

Create:

```python
class PersistingAnchoredCompactor:
    def __init__(self, inner: SemanticCompactor, sessions: object) -> None: ...

    async def compact(self, *args: object, **kwargs: object) -> SemanticCompactionResult:
        result = await self._inner.compact(*args, **kwargs)
        if result.checkpoint is not None:
            await self._sessions.create_checkpoint(
                result.checkpoint.thread_id,
                "semantic-compaction",
                semantic_checkpoint_payload(result.checkpoint),
            )
        return result
```

The payload contains stable IDs, source range/digest, model, usage, and version. It excludes full source messages.

- [ ] **Step 6: Verify Feature and integration GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/thread_intelligence/tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest tests.test_agent_app -v
```

- [ ] **Step 7: Update Unit lists and commit in two stages**

```powershell
git add src/code_agent/thread_intelligence src/code_agent/context src/code_agent/core/models.py
git commit -m "feat: build source anchored context checkpoints"
git add code_agent_win tests/test_agent_app.py
git commit -m "feat: persist semantic checkpoint facts"
```

### Task 4: Expose complete bounded Git diff facets

**Stage:** Workspace Feature implementation

**Files:**
- Modify: `src/code_agent/workspace/git.py`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `src/code_agent/workspace/tests/test_git.py`
- Modify: `src/code_agent/workspace/tests/test_git_limits.py`

- [ ] **Step 1: Write failing staged/untracked tests**

Create a temporary Git repository with one staged file, one unstaged file, and one untracked UTF-8 file. Assert:

```python
snapshot = workspace.diff_snapshot()
self.assertIn("staged.txt", snapshot.staged)
self.assertIn("unstaged.txt", snapshot.unstaged)
self.assertIn("+++ b/untracked.txt", snapshot.untracked)
self.assertEqual(snapshot.untracked_paths, ("untracked.txt",))
```

Also assert path filters are literal, output is globally bounded, binary untracked files use a metadata-only marker, and `.git` paths remain inaccessible.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest src.code_agent.workspace.tests.test_git src.code_agent.workspace.tests.test_git_limits -v
```

- [ ] **Step 3: Implement immutable GitDiffSnapshot**

```python
@dataclass(frozen=True)
class GitDiffSnapshot:
    staged: str = ""
    unstaged: str = ""
    untracked: str = ""
    untracked_paths: tuple[str, ...] = ()
```

`GitWorkspace.diff_snapshot(paths=())` uses only fixed argv:

```text
git diff --no-ext-diff --no-textconv --cached -- <paths>
git diff --no-ext-diff --no-textconv -- <paths>
git ls-files --others --exclude-standard -z -- <paths>
```

Untracked text is converted to a bounded `/dev/null -> b/path` unified diff with Python `difflib`; binary content is not decoded. All facets share the existing global output ceiling.

- [ ] **Step 4: Verify Workspace GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
git add src/code_agent/workspace
git commit -m "feat: expose complete git diff facets"
```

### Task 5: Add scope-aware Unified DiffView

**Stage:** Interfaces Feature implementation

**Files:**
- Modify: `src/code_agent/interfaces/diff_view.py`
- Modify: `src/code_agent/interfaces/tui_interactions.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Modify: `src/code_agent/interfaces/tests/test_interaction_v2.py`

- [ ] **Step 1: Write failing scope and stale-source tests**

Assert working-tree view contains staged, unstaged, and untracked sections; per-turn and since-checkpoint use recorded diff without live override; duplicate paths in separate scopes stay distinguishable; and stale recorded/live disagreement is visible.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest src.code_agent.interfaces.tests.test_interaction_v2.DiffViewTests -v
```

- [ ] **Step 3: Implement scope-aware document types**

```python
class DiffScope(str, Enum):
    WORKING_TREE = "working-tree"
    STAGED = "staged"
    UNSTAGED = "unstaged"
    UNTRACKED = "untracked"
    PER_TURN = "per-turn"
    SINCE_CHECKPOINT = "since-checkpoint"

@dataclass(frozen=True)
class DiffSourceDocument:
    scope: DiffScope
    unified: str
    fresh: bool
```

Each `FileDiff` stores its scope. `DiffController.load(scope, recorded_diff, paths)` consults live Git only for working-tree/staged/unstaged/untracked scopes. Rendered headers include scope and `fresh`/`recorded` provenance.

- [ ] **Step 4: Verify Interfaces GREEN and commit**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
git add src/code_agent/interfaces
git commit -m "feat: add scope aware diff review"
```

### Task 6: Persist guarded workspace snapshots

**Stage:** Workspace and Sessions Feature implementation

**Files:**
- Create: `src/code_agent/workspace/snapshot_store.py`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Create: `src/code_agent/workspace/tests/test_snapshot_store.py`
- Modify: `src/code_agent/sessions/_records.py`
- Modify: `src/code_agent/sessions/models.py`
- Modify: `src/code_agent/sessions/_database.py`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/sessions/tests/test_repository.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`

- [ ] **Step 1: Write failing snapshot integrity tests**

Test an existing dirty file, a missing future file, binary bytes, duplicate paths, tampered blob digest, traversal in a manifest, aggregate size limit, and restore to the exact pre-turn bytes.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest src.code_agent.workspace.tests.test_snapshot_store -v
```

- [ ] **Step 3: Implement product-state SnapshotStore**

Expose:

```python
@dataclass(frozen=True)
class SnapshotHandle:
    identifier: str
    digest: str
    paths: tuple[str, ...]
    total_bytes: int

class WorkspaceSnapshotStore:
    def save(self, snapshot: WorkspaceSnapshot) -> SnapshotHandle: ...
    def load(self, handle: SnapshotHandle) -> WorkspaceSnapshot: ...
```

Use content-addressed blobs and an atomically replaced UTF-8 JSON manifest under the injected product-state root. Validate every relative path through `WorkspacePathGuard`; never place state inside the repository or `.git`.

- [ ] **Step 4: Record checkpoint bounds**

Extend `CheckpointRecord` with optional `message_sequence` and `event_sequence`. `create_checkpoint` reads both maxima in the same transaction and stores them in dedicated migration-10 columns, not caller-controlled metadata. Old rows decode as `None` and are not rewindable.

- [ ] **Step 5: Verify Workspace/Sessions GREEN and commit separately**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
git add src/code_agent/workspace
git commit -m "feat: persist guarded workspace snapshots"
git add src/code_agent/sessions
git commit -m "feat: record durable checkpoint bounds"
```

### Task 7: Build code/conversation/both rewind previews

**Stage:** Interfaces Feature implementation, followed by integration

**Files:**
- Create: `src/code_agent/interfaces/rewind_view.py`
- Modify: `src/code_agent/interfaces/AGENTS.md`
- Create: `src/code_agent/interfaces/tests/test_rewind_view.py`
- Create: `code_agent_win/rewind_runtime.py`
- Modify: `code_agent_win/AGENTS.md`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/app_ui.py`
- Modify: `tests/test_agent_app.py`

- [ ] **Step 1: Write failing preview tests**

```python
preview = build_rewind_preview(
    RewindKind.BOTH,
    checkpoint,
    snapshot_handle,
    current_dirty_paths=("user-before.txt",),
)
self.assertEqual(preview.code_paths, ("agent-change.txt",))
self.assertGreater(preview.conversation_messages, 0)
self.assertFalse(preview.requires_git_reset)
self.assertTrue(preview.requires_confirmation)
```

Old checkpoints without bounds and missing/tampered snapshots must return a disabled preview with a stable reason.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest src.code_agent.interfaces.tests.test_rewind_view -v
```

- [ ] **Step 3: Implement side-effect-free view models**

```python
class RewindKind(str, Enum):
    CONVERSATION = "conversation"
    CODE = "code"
    BOTH = "both"

@dataclass(frozen=True)
class RewindPreview:
    kind: RewindKind
    checkpoint_id: str
    code_paths: tuple[str, ...]
    conversation_messages: int
    dirty_baseline_paths: tuple[str, ...]
    enabled: bool
    reason: str | None
    requires_confirmation: bool = True
    requires_git_reset: bool = False
```

The Interfaces layer never restores files or mutates Sessions.

- [ ] **Step 4: Add integration preview service**

`RewindRuntime.preview(thread_id, checkpoint_id, kind)` loads the checkpoint and snapshot handle, verifies the snapshot manifest, and returns `RewindPreview`. It does not expose an apply action in this P0 plan; explicit apply requires a separate policy-reviewed typed action after preview acceptance.

- [ ] **Step 5: Verify Interfaces/integration GREEN and commit separately**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest tests.test_agent_app -v
git add src/code_agent/interfaces
git commit -m "feat: add safe rewind previews"
git add code_agent_win tests/test_agent_app.py
git commit -m "feat: integrate diff and rewind projections"
```

### Task 8: Full integration verification and documentation reconciliation

**Stage:** Integration

**Files:**
- Modify: `README.md`
- Modify: `docs/amp-inspired-runtime.md`
- Modify: `docs/research/cli-tui-design-comparison.md`
- Modify: `code_agent_win/AGENTS.md`
- Test: all Feature and root suites

- [ ] **Step 1: Update only verified user-facing claims**

Document exact P0 behavior, command/view availability, snapshot location, no-hidden-`git reset` guarantee, and the distinction between rewind preview and a future policy-gated apply action. Change the research report's current-state table only for capabilities proven by tests.

- [ ] **Step 2: Run every suite**

```powershell
$python = '.\.venv\Scripts\python.exe'
Get-ChildItem src\code_agent -Directory | ForEach-Object {
    $tests = Join-Path $_.FullName 'tests'
    if (Test-Path $tests) {
        & $python -m unittest discover -s $tests -p 'test_*.py'
        if ($LASTEXITCODE -ne 0) { throw "failed suite: $tests" }
    }
}
& $python -m unittest discover -s tests -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { throw 'failed root integration suite' }
```

Expected: zero failures/errors; platform-guarded skips must remain explicitly reported.

- [ ] **Step 3: Run structural checks**

```powershell
python -m compileall -q src code_agent_win tests
git diff --check
git status --short
```

Expected: compile success, no whitespace errors, and only plan-related branch changes.

- [ ] **Step 4: Commit integration docs**

```powershell
git add README.md docs/amp-inspired-runtime.md docs/research/cli-tui-design-comparison.md code_agent_win/AGENTS.md
git commit -m "docs: record p0 cli tui recovery delivery"
```

## Acceptance gates

- Every new production behavior has a test observed RED before implementation.
- Context events derive from a request carrying the actual thread ID and positive revision.
- Semantic checkpoint metadata can resolve to a stable source range without storing source text in the event payload.
- Working-tree review includes staged, unstaged, and untracked files; per-turn data cannot be silently replaced by live Git data.
- Snapshots restore pre-turn bytes, including pre-existing dirty content, and never use `git reset`, `checkout`, or repository-local hidden state.
- Rewind is preview-only in this delivery and always requires explicit confirmation before any future apply action.
- Mode and permission remain independent; no new UI operation grants authority.
- All Feature suites and root integration tests pass from the isolated worktree.
