# Agent Context Budget And Task State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound every model prompt with one dynamic budget, retain a compact factual task state across long threads, avoid repeated repository parsing, and make tool calls self-describing and schema-validated.

**Architecture:** `AgentEngine` will obtain the tool definitions and a persistent task-state snapshot before building a context. `WorkspaceContextBuilder` will allocate one bounded prompt between rules, tools, task state, repository map, and compacted messages. A process-local repository index cache will avoid repeat parsing, while SQLite persists only durable task facts and bounded model-authored working notes.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`/`sqlite3`/`json`, existing `httpx`, `psutil`, `regex`, and `unittest`.

---

## Scope And Non-Goals

- Preserve the current uncommitted model-profile and persistent task-budget work. It is a separate change set and must not be reverted, squashed into this feature, or used to alter default agent-round/tool-call limits.
- Keep `max_agent_rounds` and `max_tool_calls` unchanged by this plan. This work only measures their consumption for later evidence-based tuning.
- Do not make a second model request to summarize history.
- Do not persist repository-map cache across process restarts and do not add a file-system watcher.
- Do not send raw prompts, source code, API keys, or command output to an external telemetry service.
- Do not silently truncate project rules. A rule set that cannot fit its configured hard allocation must fail with a typed context error that names the limit.

## Target Defaults

`PromptBudget` must use these defaults unless a caller supplies stricter values:

| Allocation | Token ceiling | Behavior |
| --- | ---: | --- |
| Total prompt | 20,000 | Hard upper bound for system prompt, rules, tools, task state, repo map, and messages. |
| Rendered rules | 3,000 | Hard allocation; exceeding it raises `RuleLimitError`. |
| Tool schemas | 1,500 | Required reservation; an oversized tool catalog raises `PromptBudgetError`. |
| Task state | 1,000 | Deterministically rendered with priority ordering. |
| Repo map | 2,000 | Best-effort allocation; it shrinks before message history. |
| Message history | 12,000 maximum, 2,000 minimum | The compactor uses the dynamically allocated amount, never more than 12,000. |
| Safety reserve | 500 | Kept unused to absorb estimator error. |

The actual message budget for a turn is:

```text
max_message_tokens = min(
    12,000,
    20,000 - fixed_system_and_rules - tool_schemas - task_state - repo_map - 500
)
```

If a 2,000-token message minimum would not fit, the repo map shrinks first. If it still does not fit, context construction fails before any model request.

## File Map

| Path | Responsibility |
| --- | --- |
| `src/code_agent/context/budget.py` | Immutable prompt-budget configuration and deterministic allocation. |
| `src/code_agent/context/cache.py` | Process-local source-index cache, signatures, invalidation, and bounded capacity. |
| `src/code_agent/context/models.py` | Extend context configuration and result types with budget-aware values. |
| `src/code_agent/context/builder.py` | Build a budgeted prompt from rules, tools, task state, repo map, and compacted messages. |
| `src/code_agent/context/repo_map.py` | Consume the cache and rank cached entries for the current task. |
| `src/code_agent/context/task_state.py` | Pure task-state rendering and deterministic trimming. |
| `src/code_agent/core/models.py` | Define JSON-safe immutable `TaskState` and `TaskStateUpdate` values. |
| `src/code_agent/core/protocols.py` | Extend context and session protocols with tools and task-state methods. |
| `src/code_agent/core/engine.py` | Obtain tools/state before context build and emit bounded usage metrics. |
| `src/code_agent/sessions/_database.py` | Migration for task-state persistence. |
| `src/code_agent/sessions/repository.py` | Read, atomically update, and reduce persistent task state. |
| `code_agent_win/tools.py` | Single source of truth for tool schemas and typed argument validation. |
| `code_agent_win/app.py` | Wire schemas, state updates, cache invalidation, and context dependencies. |
| `README.md` | Document budgets, configuration, cache behavior, and locally stored metrics. |
| Feature `AGENTS.md` files | Record each new public unit and its boundary. |

## Task 1: Establish Context-Budget Contracts

**Files:**
- Create: `src/code_agent/context/budget.py`
- Modify: `src/code_agent/context/models.py`
- Modify: `src/code_agent/context/errors.py`
- Modify: `src/code_agent/context/AGENTS.md`
- Create: `src/code_agent/context/tests/test_budget.py`

- [ ] **Step 1: Write boundary tests for deterministic allocation.**

```python
from code_agent.context.budget import PromptBudget, PromptBudgetError


def test_allocation_shrinks_repo_map_before_messages() -> None:
    budget = PromptBudget()
    allocation = budget.allocate(
        system_and_rules_tokens=3_000,
        tool_tokens=1_500,
        task_state_tokens=1_000,
    )

    assert allocation.repo_map_tokens == 2_000
    assert allocation.message_tokens == 12_000
    assert allocation.total_tokens == 20_000


def test_allocation_rejects_fixed_content_that_cannot_preserve_messages() -> None:
    budget = PromptBudget()

    with pytest.raises(PromptBudgetError, match="minimum message allocation"):
        budget.allocate(
            system_and_rules_tokens=16_500,
            tool_tokens=1_500,
            task_state_tokens=1_000,
        )
```

Use `unittest.TestCase` rather than `pytest`; the assertions above define the required behavior. Add tests for invalid integer values, a zero-token repo map, and exact 20,000-token boundary behavior.

- [ ] **Step 2: Run the new test module before implementation.**

Run:

```powershell
python -m unittest src/code_agent/context/tests/test_budget.py -v
```

Expected: import failure because `code_agent.context.budget` does not exist.

- [ ] **Step 3: Implement the immutable budget types.**

Define these public values exactly:

Create immutable `PromptAllocation` fields named `rule_tokens`, `tool_tokens`, `task_state_tokens`, `repo_map_tokens`, `message_tokens`, and `safety_tokens`. Its `total_tokens` property returns the sum of those six integer fields.

Create immutable `PromptBudget` fields named `max_prompt_tokens`, `max_rule_tokens`, `max_tool_tokens`, `max_task_state_tokens`, `max_repo_map_tokens`, `max_message_tokens`, `min_message_tokens`, and `safety_tokens`, with the target defaults in this document. Its public allocation method accepts keyword-only `system_and_rules_tokens`, `tool_tokens`, and `task_state_tokens` integers and returns `PromptAllocation`.

`allocate` must reject negative inputs, reject rule/tool/state values over their individual ceilings, reserve `safety_tokens`, allocate the repository map only from space remaining after the minimum message allocation, and verify the returned sum equals `max_prompt_tokens` or less. Add `PromptBudgetError(ContextError)` to `errors.py`.

- [ ] **Step 4: Extend `ContextConfig` with `prompt_budget: PromptBudget`.**

Use `field(default_factory=PromptBudget)` so existing `ContextConfig` construction remains valid. Keep the existing `repo_map_tokens` and `message_tokens` constructor arguments only as deprecated compatibility aliases for this change; normalize them in `__post_init__` into a new `PromptBudget` and reject conflicting values. Update `AGENTS.md` with `PromptBudget.allocate` and `PromptAllocation`.

- [ ] **Step 5: Run all context-model and budget tests.**

Run:

```powershell
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
```

Expected: PASS, including existing `ContextConfig` callers.

- [ ] **Step 6: Commit only the context-budget contract.**

```powershell
git add src/code_agent/context/budget.py src/code_agent/context/models.py src/code_agent/context/errors.py src/code_agent/context/AGENTS.md src/code_agent/context/tests/test_budget.py
git commit -m "feat: add bounded prompt allocation"
```

## Task 2: Build Context From the Dynamic Allocation

**Files:**
- Modify: `src/code_agent/context/builder.py`
- Modify: `src/code_agent/context/compaction.py`
- Modify: `src/code_agent/context/rules.py`
- Modify: `src/code_agent/context/tests/test_builder.py`
- Modify: `src/code_agent/context/tests/test_compaction.py`
- Modify: `src/code_agent/context/tests/test_rules.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/tests/_engine_support.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Write failing integration tests for the allocation order.**

Add tests proving all of the following:

```python
async def test_builder_limits_messages_using_tool_and_rule_reservations() -> None:
    bundle = await builder.build(
        messages=long_history,
        user_input="fix the failing test",
        tools=tools,
        task_state=TaskState.empty(),
    )

    assert estimated_message_tokens(bundle.messages) <= expected_message_budget
    assert "Repository map:" in bundle.system_prompt


async def test_builder_rejects_rules_that_exceed_the_rule_ceiling() -> None:
    with self.assertRaisesRegex(RuleLimitError, "3,000"):
        await oversized_rule_builder.build((), "inspect", (), TaskState.empty())
```

Use the project token estimator, not a hard-coded character count. Add an engine test that verifies tools are acquired before `ContextBuilder.build` and passed unchanged to the model client.

- [ ] **Step 2: Change the protocol before implementation.**

Replace the `ContextBuilder` signature with:

Change `ContextBuilder.build` to accept four positional inputs in this order: `messages: Sequence[Message]`, `user_input: str`, `tools: Sequence[ToolDefinition]`, and `task_state: TaskState`; it returns `ContextBundle` asynchronously.

Update all test fakes in `_engine_support.py` in the same change. Do not provide a variadic compatibility path; an omitted tool/state input must fail type-level tests.

- [ ] **Step 3: Implement deterministic token accounting in `WorkspaceContextBuilder`.**

Render rules first and calculate their token cost. If it exceeds `max_rule_tokens`, raise `RuleLimitError` before calling the model. Serialize tool definitions with `json.dumps(tool.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))`, calculate their total token cost, render the bounded task state, and call `PromptBudget.allocate`.

Render the repo map using `allocation.repo_map_tokens`, then call `DeterministicCompactor.compact(messages, token_budget=allocation.message_tokens)`. Construct the system prompt in this fixed order:

```text
base system prompt

project rules

task state

repository map
```

The builder must assert that the estimator sees no more than `max_prompt_tokens - safety_tokens` for all rendered components and compacted messages.

- [ ] **Step 4: Modify `AgentEngine` call order without changing round/tool limits.**

Obtain `tools = tuple(self._actions.tools())` and validate duplicate names before invoking `ContextBuilder.build`. Pass the same `tools` tuple into both context construction and `self._model.stream`. Do not change `EngineLimits`, `TaskBudget`, `ModelProfile`, or their defaults in this task.

- [ ] **Step 5: Run focused regressions.**

Run:

```powershell
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: PASS. Confirm that the existing persisted task-budget tests remain unchanged and passing.

- [ ] **Step 6: Commit the budgeted context integration.**

```powershell
git add src/code_agent/context/builder.py src/code_agent/context/compaction.py src/code_agent/context/rules.py src/code_agent/context/tests src/code_agent/core/protocols.py src/code_agent/core/engine.py src/code_agent/core/tests
git commit -m "feat: build model contexts from dynamic budgets"
```

## Task 3: Add a Bounded Repository-Map Cache

**Files:**
- Create: `src/code_agent/context/cache.py`
- Modify: `src/code_agent/context/repo_map.py`
- Modify: `src/code_agent/context/builder.py`
- Modify: `src/code_agent/context/AGENTS.md`
- Create: `src/code_agent/context/tests/test_cache.py`
- Modify: `src/code_agent/context/tests/test_repo_map.py`
- Modify: `code_agent_win/app.py`
- Modify: `tests/test_agent_app.py`

- [ ] **Step 1: Write cache correctness tests.**

Cover these executable cases:

```python
def test_second_build_reuses_unchanged_python_parse_result() -> None:
    first = builder.render("parse_widget", (), 2_000)
    second = builder.render("parse_widget", (), 2_000)

    self.assertEqual(first, second)
    self.assertEqual(parser.call_count, 1)


def test_changed_file_is_reparsed_and_reranked() -> None:
    write_text(root / "widget.py", "def replacement(): pass\n")

    rendered = builder.render("replacement", ("widget.py",), 2_000)

    self.assertIn("replacement", rendered)
    self.assertEqual(parser.call_count, 2)


def test_dispatcher_write_invalidates_the_written_path() -> None:
    await dispatcher.dispatch(write_request, CancellationToken())

    self.assertTrue(cache.was_invalidated("widget.py"))
```

- [ ] **Step 2: Implement a process-local cache keyed by file signature.**

Create these values in `cache.py`:

Create immutable `FileSignature(size_bytes: int, modified_ns: int)` and `CachedScan(signature: FileSignature, entry: RepoEntry)` values. Create `RepoMapCache` methods named `get_or_scan(path, scan) -> RepoEntry`, `invalidate(paths) -> None`, and `clear() -> None`.

Use `stat().st_size` and `stat().st_mtime_ns` as the signature. Store at most 5,000 entries; evict the oldest access entry when capacity is exceeded. Never cache unreadable, binary, or parse-failed source text as a successful symbol parse.

- [ ] **Step 3: Make `RepoMapBuilder` cache-aware without changing ranking behavior.**

Inject `RepoMapCache` into `RepoMapBuilder` with a default newly created cache. Keep the current scan bounds, language parsing, dependency resolution, and `_rank` behavior. A cache hit may skip parsing, but `build(query, touched_files)` must always rerank entries for its current arguments.

- [ ] **Step 4: Wire explicit invalidation after successful workspace edits.**

Create one `RepoMapCache` in `create_application`, pass it to `RepoMapBuilder`, and pass its `invalidate` method to `RootActionDispatcher`. After a successful `write_file` or `replace_text`, invalidate exactly `plan.relative_path`. Do not invalidate on denied or failed edits.

- [ ] **Step 5: Run cache and integration tests.**

Run:

```powershell
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: PASS, and the test double proves no second parse for an unchanged file.

- [ ] **Step 6: Commit repository-map caching.**

```powershell
git add src/code_agent/context/cache.py src/code_agent/context/repo_map.py src/code_agent/context/builder.py src/code_agent/context/AGENTS.md src/code_agent/context/tests src/code_agent_win/app.py tests/test_agent_app.py
git commit -m "feat: cache repository map parsing"
```

## Task 4: Persist a Structured Task State

**Files:**
- Create: `src/code_agent/core/task_state.py`
- Modify: `src/code_agent/core/models.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/AGENTS.md`
- Create: `src/code_agent/core/tests/test_task_state.py`
- Modify: `src/code_agent/sessions/_database.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/sessions/_codec.py`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`
- Modify: `src/code_agent/sessions/tests/test_repository.py`
- Create: `src/code_agent/context/task_state.py`
- Modify: `src/code_agent/context/builder.py`
- Create: `src/code_agent/context/tests/test_task_state.py`

- [ ] **Step 1: Write reducer and persistence tests.**

Test exact facts, not a free-form summary:

```python
def test_reducer_records_successful_read_write_and_failed_command() -> None:
    state = TaskState(objective="repair startup")
    state = reduce_task_state(state, read_file_request, read_file_result)
    state = reduce_task_state(state, write_file_request, write_file_result)
    state = reduce_task_state(state, command_request, failed_command_result)

    self.assertEqual(state.files_read, ("src/app.py",))
    self.assertEqual(state.files_changed, ("src/app.py",))
    self.assertEqual(state.failed_commands[0].returncode, 1)


async def test_task_state_survives_repository_reopen() -> None:
    await repository.save_task_state(thread_id, state)
    reopened = SQLiteSessionRepository(database_path)

    self.assertEqual(await reopened.load_task_state(thread_id), state)
```

Add a migration test from schema version 3 and corruption tests for malformed state JSON. Add a rendering test that proves objective, failed commands, changed files, and open questions are preserved ahead of lower-priority facts when the 1,000-token state budget is exceeded.

- [ ] **Step 2: Define immutable, JSON-safe task-state values.**

Create `task_state.py` with these public types:

Create immutable `CommandFact` with `command: str`, `returncode: int | None`, and `reason: str`. Create immutable `TaskState` with `objective`, `files_read`, `files_changed`, `failed_commands`, `verified_facts`, `working_notes`, and `open_questions`. Create immutable `TaskStateUpdate` with `verified_facts`, `working_notes`, and `open_questions`. `TaskState.empty()` returns the valid zero-fact state used before the first request.

Bound every sequence to 32 values and every individual string to 1,024 Unicode code points. `working_notes` must render as unverified; `verified_facts` must only be populated by deterministic reducer output or an explicitly verified update.

- [ ] **Step 3: Add schema version 4 and atomic repository methods.**

Add a migration that creates:

```sql
CREATE TABLE task_states (
    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

Add `SessionRepository` and SQLite methods named `load_task_state(thread_id) -> TaskState`, `save_task_state(thread_id, state) -> None`, and `reduce_task_state(thread_id, request, result) -> TaskState`; all three methods are asynchronous.

`reduce_task_state` must execute one database write transaction: load existing state, apply the pure reducer, serialize the result, and update `updated_at`. A missing task-state row loads as `TaskState.empty()`; a missing thread remains `SessionNotFound`.

- [ ] **Step 4: Load and render state on every agent turn.**

Before `ContextBuilder.build`, `AgentEngine` loads the thread task state and passes it through the context protocol. After each completed workspace or command action, the engine calls the repository reducer before adding the tool result to the next turn's messages. `WorkspaceContextBuilder` uses `context.task_state.render_task_state(state, token_budget)` and adds this exact heading when non-empty:

```text
Task state (facts are verified; working notes are unverified):
```

- [ ] **Step 5: Run core, session, and context tests.**

Run:

```powershell
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
```

Expected: PASS, including a reopened SQLite database carrying the same task facts into a later model turn.

- [ ] **Step 6: Commit structured state persistence.**

```powershell
git add src/code_agent/core/task_state.py src/code_agent/core/models.py src/code_agent/core/protocols.py src/code_agent/core/engine.py src/code_agent/core/AGENTS.md src/code_agent/core/tests src/code_agent/sessions/_database.py src/code_agent/sessions/repository.py src/code_agent/sessions/_codec.py src/code_agent/sessions/AGENTS.md src/code_agent/sessions/tests src/code_agent/context/task_state.py src/code_agent/context/builder.py src/code_agent/context/tests
git commit -m "feat: persist structured agent task state"
```

## Task 5: Define and Validate Tool Schemas

**Files:**
- Create: `code_agent_win/tools.py`
- Modify: `code_agent_win/app.py`
- Modify: `src/code_agent/policy/classifier.py`
- Modify: `src/code_agent/policy/AGENTS.md`
- Modify: `tests/test_agent_app.py`
- Create: `tests/test_tool_schemas.py`

- [ ] **Step 1: Write schema and dispatcher tests.**

Cover each current public tool and the state-update tool:

```python
def test_replace_text_schema_requires_exact_replacement_fields() -> None:
    with self.assertRaisesRegex(ValueError, "old_text"):
        parse_tool_arguments("replace_text", {"path": "a.py", "new_text": "x"})


def test_schema_rejects_unknown_properties() -> None:
    with self.assertRaisesRegex(ValueError, "unexpected property"):
        parse_tool_arguments("read_file", {"path": "a.py", "mode": "binary"})


async def test_task_state_update_is_persisted_without_workspace_approval() -> None:
    result = await dispatcher.dispatch(task_state_request, CancellationToken())

    self.assertFalse(result.is_error)
    self.assertIn("working_notes", (await sessions.load_task_state(thread_id)).to_dict())
```

- [ ] **Step 2: Make `code_agent_win/tools.py` the single source of truth.**

Expose:

Expose `tool_definitions()`, which returns an immutable sequence of `ToolDefinition`, and `parse_tool_arguments(name, arguments)`, which returns a JSON-safe argument mapping, from `code_agent_win.tools`.

Define JSON Schemas with `additionalProperties: false` for exactly these names: `read_file`, `list_files`, `search_text`, `write_file`, `replace_text`, `git_status`, `git_diff`, `run_command`, and `update_task_state`.

Required fields:

| Tool | Required fields |
| --- | --- |
| `read_file` | `path` |
| `list_files` | none |
| `search_text` | `pattern` |
| `write_file` | `path`, `content` |
| `replace_text` | `path`, `old_text`, `new_text` |
| `git_status` | none |
| `git_diff` | none; optional `paths` array |
| `run_command` | `command` |
| `update_task_state` | none; optional bounded arrays `working_notes`, `open_questions` |

Implement validation without a new dependency. Validate JSON types, required keys, unknown keys, non-empty strings, and array item types. The schema returned by `tool_definitions()` and the parser must use shared constants, not duplicated maps.

- [ ] **Step 3: Route all dispatcher input through the parser.**

Replace the locally constructed `ToolDefinition` tuple in `RootActionDispatcher.tools()` with `tool_definitions()`. At the top of `_execute`, parse the request arguments once and use only parsed values. Parser failure returns a structured `ActionResult` error without entering file, Git, runtime, or session code.

- [ ] **Step 4: Add bounded model-authored notes.**

`update_task_state` updates only `working_notes` and `open_questions`; it cannot write `verified_facts`, `files_read`, `files_changed`, or command results. Classify it as a low-risk session-state tool that is allowed in `plan`, `ask`, and `auto` modes, while preserving event auditing. Its request and result must use the same bounded `TaskStateUpdate` parser defined in `core.task_state`.

- [ ] **Step 5: Run application and policy tests.**

Run:

```powershell
python -m unittest discover -s src/code_agent/policy/tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: PASS. Confirm malformed tool calls do not create a file, spawn a process, or alter task state.

- [ ] **Step 6: Commit schema-driven tools.**

```powershell
git add code_agent_win/tools.py code_agent_win/app.py src/code_agent/policy/classifier.py src/code_agent/policy/AGENTS.md tests/test_agent_app.py tests/test_tool_schemas.py
git commit -m "feat: validate agent tool schemas"
```

## Task 6: Add Local Measurements And Document Operations

**Files:**
- Modify: `src/code_agent/core/events.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/context/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `code_agent_win/app.py`
- Modify: `README.md`
- Modify: `tests/test_agent_app.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Write event-payload tests for redacted measurements.**

```python
async def test_context_event_records_budget_breakdown_without_prompt_text() -> None:
    events = [event async for event in engine.run("inspect secret.txt")]
    context_event = next(event for event in events if event.kind is EventKind.CONTEXT_BUILT)

    self.assertEqual(context_event.payload["prompt_tokens"], 20_000)
    self.assertNotIn("secret.txt contents", str(context_event.payload))
    self.assertIn("repo_map_tokens", context_event.payload)
```

- [ ] **Step 2: Extend the existing `CONTEXT_BUILT` event payload.**

Record only integer counters: total configured prompt budget, rules, tools, task state, repo map, compacted messages, removed message count, and cache hits/misses. Do not add source text, rendered rules, command output, API key names, or model prompts to events.

- [ ] **Step 3: Document configuration and diagnosis.**

Document the target defaults, rule-overflow behavior, cache lifetime, task-state fact versus note semantics, and the local-only measurement fields in `README.md`. Explain that agent-round and tool-call values are retained from the existing profile/budget work and are deliberately not tuned by this feature.

- [ ] **Step 4: Run the full test matrix and package build.**

Run:

```powershell
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/policy/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/providers/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/runtime/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python -m build
```

Expected: all tests pass and `dist/` contains both a wheel and source distribution.

- [ ] **Step 5: Perform a manual smoke test with a fake provider.**

Use the existing fake-model integration harness to run this deterministic sequence: `read_file`, `write_file`, failed `run_command`, `update_task_state`, final text response. Confirm the second model turn receives a prompt budget breakdown, the changed file is re-indexed, and a resumed thread restores the same structured state.

- [ ] **Step 6: Commit measurement and documentation work.**

```powershell
git add src/code_agent/core/events.py src/code_agent/core/engine.py src/code_agent/core/AGENTS.md src/code_agent/context/AGENTS.md src/code_agent/sessions/AGENTS.md code_agent_win/app.py README.md tests/test_agent_app.py src/code_agent/core/tests/test_engine_run.py
git commit -m "docs: document bounded agent context operations"
```

## Acceptance Criteria

- Every context build has an estimated token total at or below `PromptBudget.max_prompt_tokens - PromptBudget.safety_tokens`.
- Oversized project rules fail before provider invocation and name the rule-allocation limit.
- The repository map does not reparse unchanged source files between model turns, but a successful write causes the next turn to parse and rank that file again.
- A resumed thread restores its objective, read files, changed files, failed commands, verified facts, unverified notes, and open questions.
- The task-state renderer never exceeds 1,000 estimated tokens and labels unverified notes explicitly.
- Each public tool exposes a strict JSON Schema and malformed calls cause no workspace, process, Git, or session side effect.
- Metrics contain numeric allocation and cache data only, with no prompt or command-output content.
- Existing model-round/tool-call profile and persistent-budget tests remain passing without changing their configured values.

## Plan Self-Review

- Scope coverage: unified prompt budgeting, dynamic compaction, map caching, state persistence, strict schemas, observability, and validation are each assigned to a task.
- Dependency order: contracts precede context integration; cache uses the finished builder interface; state precedes the state-update tool; metrics document completed behavior.
- Workspace safety: no task requires a destructive Git operation or external service. Repository cache is ephemeral and task state uses versioned SQLite migration.
- Existing worktree safety: the plan explicitly avoids modifying the current profile/task-budget change set and stages files by path for every proposed commit.
