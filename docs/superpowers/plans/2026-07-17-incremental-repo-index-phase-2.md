# Incremental Repo Index Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one shared in-process repository fact index that updates only changed paths, while every model turn creates a cheap, token-bounded repository view from an immutable index snapshot.

**Architecture:** Split source parsing, repository fact ownership, and per-turn ranking into `RepoFileScanner`, `RepoIndexService`, and `RepoMapViewBuilder`. `RepoIndexService` publishes immutable, monotonically versioned snapshots and accepts exact dirty paths or a full-reconcile signal. Main and child agents share the same service; greetings and no-project workspaces continue to bypass it.

**Tech Stack:** Python 3.10+, frozen dataclasses, thread-safe in-process state, `unittest`.

**Scope boundary:** No SQLite persistence, filesystem watcher, embedding/vector store, or L3/L4 architecture expansion in this phase. The shared worktree already contains unrelated user changes, so this plan does not create commits.

---

### Task 1: Define immutable file facts and single-file scanning

**Files:**
- Create: `src/code_agent/context/repo_scan.py`
- Create: `src/code_agent/context/tests/test_repo_scan.py`

- [x] **Step 1: Write failing scanner tests**

```python
facts = RepoFileScanner(files).scan("pkg/service.py")
self.assertEqual(facts.path, "pkg/service.py")
self.assertEqual(facts.symbols[0].name, "Service")
self.assertEqual(facts.imports[0].module, "pkg.base")
self.assertGreater(facts.signature.modified_ns, 0)
```

Also verify syntax/binary failures become path-only facts tied to the current file signature.

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest src.code_agent.context.tests.test_repo_scan -v`

Expected: import failure because `repo_scan.py` does not exist.

- [x] **Step 3: Implement the scanner**

```python
@dataclass(frozen=True)
class RepoFileFacts:
    path: str
    signature: FileSignature
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[ImportRef, ...] = ()
    size_bytes: int = 0

class RepoFileScanner:
    def scan(self, path: str) -> RepoFileFacts:
        ...
```

Move the existing bounded Python AST and conservative declaration parsing into this unit without changing supported languages or limits.

- [x] **Step 4: Run scanner tests and context regression tests**

Run: `python -m unittest src.code_agent.context.tests.test_repo_scan src.code_agent.context.tests.test_repo_map -v`

Expected: all pass.

### Task 2: Build the shared incremental Repo Index

**Files:**
- Create: `src/code_agent/context/repo_index.py`
- Create: `src/code_agent/context/tests/test_repo_index.py`

- [x] **Step 1: Write failing index tests**

```python
first = index.snapshot_for_turn()
second = index.snapshot_for_turn()
self.assertEqual(first.generation, 1)
self.assertIs(first, second)
self.assertEqual(scan_counts, {"a.py": 1, "b.py": 1})

index.invalidate(("a.py",))
updated = index.snapshot_for_turn()
self.assertEqual(updated.generation, 2)
self.assertEqual(scan_counts, {"a.py": 2, "b.py": 1})
```

Add separate tests for add/delete, dependency re-resolution, full reconciliation after `invalidate(())`, and concurrent first access publishing only one generation.

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest src.code_agent.context.tests.test_repo_index -v`

Expected: import failure because `RepoIndexService` and `RepoIndexSnapshot` do not exist.

- [x] **Step 3: Implement immutable snapshots and incremental updates**

```python
@dataclass(frozen=True)
class RepoIndexSnapshot:
    generation: int
    entries: tuple[RepoEntry, ...]

class RepoIndexService:
    def snapshot_for_turn(self) -> RepoIndexSnapshot: ...
    def invalidate(self, paths: Sequence[str]) -> None: ...
```

Use a short state lock plus a separate update lock: invalidation remains non-blocking while only one thread initializes or refreshes the index. Exact paths compare signatures and parse only changed files. An empty invalidation performs one bounded inventory reconciliation on the next code turn.

- [x] **Step 4: Run index tests**

Run: `python -m unittest src.code_agent.context.tests.test_repo_index -v`

Expected: all pass.

### Task 3: Make Turn Repo Map a pure snapshot query

**Files:**
- Modify: `src/code_agent/context/repo_map.py`
- Modify: `src/code_agent/context/tests/test_repo_map.py`

- [x] **Step 1: Write failing view tests**

```python
builder.build(query="alpha")
with patch.object(files, "list_files", side_effect=AssertionError):
    ranked = builder.build(query="beta")
self.assertEqual(ranked[0].path, "beta.py")
```

Add a test proving `(generation, normalized query, touched paths, token budget)` controls a bounded view LRU: same generation/query hits; an index generation change misses and exposes updated symbols.

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest src.code_agent.context.tests.test_repo_map -v`

Expected: the second build accesses workspace inventory and the generation-aware view API is absent.

- [x] **Step 3: Implement pure ranking and rendering**

```python
class RepoMapViewBuilder:
    def build(self, snapshot, query, touched_files): ...
    def render(self, snapshot, query, touched_files, token_budget): ...

class RepoMapBuilder:
    def render_with_metrics(self, query, touched_files, token_budget):
        snapshot = self.index.snapshot_for_turn()
        return self.view_cache.get_or_build(...)
```

Retain the public `RepoMapBuilder.build/render` compatibility facade, but prohibit it from scanning once the index snapshot is current.

- [x] **Step 4: Run repo-map and context-builder tests**

Run: `python -m unittest src.code_agent.context.tests.test_repo_map src.code_agent.context.tests.test_builder -v`

Expected: all pass.

### Task 4: Feed task-local touched paths into the Turn View

**Files:**
- Modify: `src/code_agent/context/builder.py`
- Modify: `src/code_agent/context/tests/test_builder.py`
- Modify: `src/code_agent/core/models.py`

- [x] **Step 1: Write failing context tests**

```python
state = TaskState(files_read=("src/read.py",), files_changed=("src/changed.py",))
await builder.build((), "repair", (), state)
self.assertEqual(repo_map.seen_touched, ("src/changed.py", "src/read.py"))
```

Update cache measurement expectations from per-file parse-cache counts to per-turn view-cache counts.

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest src.code_agent.context.tests.test_builder -v`

Expected: touched paths are currently passed as `()`.

- [x] **Step 3: Integrate the view API**

Call `render_with_metrics` with the stable union of changed/read paths. Preserve all existing prompt budgets, greeting/no-project bypass, and numeric-only measurements.

- [x] **Step 4: Run context and core tests**

Run: `python -m unittest discover -s src/code_agent/context/tests`

Run: `python -m unittest discover -s src/code_agent/core/tests`

Expected: all pass.

### Task 5: Share one index across main and child agents

**Files:**
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/AGENTS.md`
- Modify: `tests/test_agent_app.py`
- Modify: `tests/test_app_dispatcher.py`

- [x] **Step 1: Write failing integration tests**

```python
application = create_application(root)
self.assertIsNotNone(application.repo_index)

before = application.repo_index.snapshot_for_turn()
await application.dispatcher.dispatch(write_request, token)
after = application.repo_index.snapshot_for_turn()
self.assertGreater(after.generation, before.generation)
```

Verify exact writes refresh one path and command/verification invalidation requests one bounded reconciliation.

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_agent_app tests.test_app_dispatcher -v`

Expected: `Application.repo_index` is absent and context builders construct private indexes.

- [x] **Step 3: Wire shared services**

Construct one `RepoFileScanner`, one `RepoIndexService`, and one bounded view cache in `create_application`; inject them into every `context_for(mode)` call. Route the existing invalidation callback to both workspace inventory invalidation and `RepoIndexService.invalidate`.

- [x] **Step 4: Run root integration tests**

Run: `python -m unittest discover -s tests`

Expected: all pass.

### Task 6: Contract, verify, benchmark, and review

**Files:**
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `code_agent_win/AGENTS.md`

- [x] **Step 1: Record implemented Units**

Document scanner, index snapshot/service, pure turn view, shared ownership, dirty-path semantics, and the explicit absence of disk persistence.

- [x] **Step 2: Run the complete test suite**

Run every directory returned by:

```powershell
rg --files src/code_agent |
  Where-Object { $_ -match '[\\/]tests[\\/]test_.*\.py$' } |
  ForEach-Object { Split-Path $_ -Parent } |
  Sort-Object -Unique
```

Then run: `python -m unittest discover -s tests`

Expected: zero failures.

- [x] **Step 3: Benchmark filesystem isolation**

Measure first initialization, unchanged second turn, one-file invalidation, and Home no-project mode. Assert the unchanged second turn performs zero calls to `WorkspaceFiles.list_files`, `read_text`, and path `stat` through the indexer.

- [x] **Step 4: Request independent code review**

Review only Phase 2 files against this plan and `docs/research/incremental-repo-map-design.md`. Fix every Critical/Important issue and rerun affected tests.
