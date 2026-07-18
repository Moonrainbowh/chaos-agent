# Incremental Workspace Context Implementation Plan

> **Goal:** Remove the per-turn full workspace traversal that makes Chaos Agent appear hung in broad directories, while preserving useful repository context in real projects and making the active phase visible in the TUI.

**Architecture:** Classify the selected directory once at application composition time. Git worktrees and directories with direct project markers may build a repository map; broad ordinary directories use rules and typed tools only. Cache bounded workspace inventories in-process with a five-minute TTL and explicit invalidation after agent writes or commands. Keep file-fact invalidation in `RepoMapCache`, and skip automatic repository maps for greetings.

**Tech Stack:** Python 3.10+, `unittest`, Windows PowerShell integration.

---

## Task 1: Contract the new context behavior

**Files:**
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `src/code_agent/workspace/AGENTS.md`
- Modify: `code_agent_win/AGENTS.md`

1. Record that automatic repo maps are conditional and greetings do not trigger them.
2. Record the bounded in-process inventory cache and explicit invalidation contract.
3. Record the integration-layer project classification policy.

## Task 2: Cache and invalidate workspace inventories

**Files:**
- Modify: `src/code_agent/workspace/tests/test_files.py`
- Modify: `src/code_agent/workspace/files.py`
- Modify: `tests/test_app_dispatcher.py`
- Modify: `code_agent_win/action_dispatcher.py`

1. Add failing tests proving equal bounded listings reuse one traversal and invalidation forces a refresh.
2. Add a thread-safe, TTL-bounded cache for workspace-root listings only; explicit external-root listings remain uncached.
3. Add a failing dispatcher test proving successful commands invalidate workspace-derived caches.
4. Invalidate the combined cache after successful typed writes and completed commands.

## Task 3: Remove rule discovery's repository traversal

**Files:**
- Modify: `src/code_agent/context/tests/test_rules.py`
- Modify: `src/code_agent/context/rules.py`

1. Add a failing test proving root extension discovery does not call `WorkspaceFiles.list_files`.
2. Discover only direct `AGENTS.*.md` children of the guarded root, then keep the existing cwd ancestor chain.

## Task 4: Make automatic repository maps conditional

**Files:**
- Modify: `src/code_agent/context/tests/test_builder.py`
- Modify: `src/code_agent/context/models.py`
- Modify: `src/code_agent/context/builder.py`

1. Add failing tests for `repo_map_enabled=False` and greeting prompts.
2. Add a validated `repo_map_enabled` context option.
3. Skip the map scan for disabled contexts and recognized greetings while preserving rules, tools, task state, and messages.

## Task 5: Classify project directories at integration time

**Files:**
- Create: `code_agent_win/workspace_context.py`
- Create: `tests/test_workspace_context.py`
- Modify: `code_agent_win/app.py`

1. Add failing tests for Git worktrees, direct manifest markers, ordinary folders, and home/disk roots.
2. Implement bounded direct-child marker detection with no recursive scan.
3. Pass the classification into every main and child context builder.
4. Wire one invalidator that refreshes both repository file facts and the workspace inventory.

## Task 6: Expose truthful TUI phases

**Files:**
- Modify: `src/code_agent/interfaces/tests/test_terminal_state.py`
- Modify: `src/code_agent/interfaces/tests/test_terminal_tail.py`
- Modify: `src/code_agent/interfaces/terminal_state.py`
- Modify: `src/code_agent/interfaces/terminal_status.py`

1. Add failing tests for `TURN_STARTED → building_context` and `CONTEXT_BUILT/MODEL_STARTED → waiting_model`.
2. Add Chinese and English status presentations for both phases.
3. Preserve action, completion, cancellation, and task-state precedence.

## Task 7: Verify the focused behavior and regression surface

1. Run focused workspace, context, interface, dispatcher, and integration tests.
2. Run the full test suite.
3. Benchmark two consecutive context builds in a temporary project and a greeting build with repo maps disabled; confirm the second project build does not traverse again and the disabled greeting performs no repository scan.
4. Inspect `git diff` to ensure unrelated interface changes remain intact.
