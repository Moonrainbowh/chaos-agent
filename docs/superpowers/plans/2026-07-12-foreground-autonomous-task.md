# 前台自主任务闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows Terminal 中的工程请求以一个持久、受预算约束、可暂停和可恢复的前台自主任务执行，而不引入 daemon、终端关闭后继续执行、远程控制或自动 Git 提交。

**Architecture:** 复用现有 `AgentEngine` 的持久 thread、`TaskState` 与 `TaskBudget` 基础，在 core 中补充任务契约、生命周期和前台监督器；sessions 持久化任务聚合与累计额度；policy 接受显式任务授权；interfaces 仅投影任务事件并发出控制请求。TUI 仍是单栏连续叙事，终端退出时任务安全中断并从已持久化 checkpoint 恢复，而不是转入后台。

**Tech Stack:** Python 3.10+, `asyncio`, SQLite/WAL, `unittest`, Windows Terminal ANSI/`msvcrt`, existing `httpx` provider adapters, `psutil` runtime.

---

## Product Decisions Locked By This Plan

- 不实现 daemon、终端关闭后继续执行、手机端观察、云端任务、多任务并发、worktree、自动 commit 或 push。
- 当前 TUI 中的每次工程请求都在内部拥有一个 `TaskRecord`；普通只读问答可以很快完成，且不必在界面上突出显示任务 ID。
- 任务启动即授予当前工作区内的普通读、写、测试和重试权限。网络、工作区外路径、敏感路径、未知工具和 critical 命令仍不可自行执行。
- 任务不是无限循环：累计 token、模型轮数、工具调用、活跃时间、修复-验证循环和重复失败签名均有上限；触发后创建 checkpoint 并暂停。
- `Esc` 表示暂停当前任务；显式停止表示终止；TUI 退出和异常关闭表示可恢复中断。恢复绝不重放未确认完成的命令。
- 中文 Windows 默认使用中文 UI，支持英文切换。代码、路径、命令、Git ref、模型名和原始工具输出保持原样。

## Baseline And Dependency Gate

The worktree already contains uncommitted, in-progress foundations that this plan must reuse rather than replace:

- `core/task_state.py` persists bounded facts and is injected into context.
- `core/limits.py`, `sessions/repository.py`, and schema v4 persist model-round/tool-call task budgets.
- `docs/superpowers/plans/2026-07-11-agent-context-budget-and-task-state.md` describes the companion context-budget/task-state work.
- `src/code_agent/config/` and `code_agent_win/cli.py` contain concurrent local API configuration work; do not revert it or overwrite its composition API.

Before implementation, capture the current revision and run the tests owned by those in-progress changes. If their public signatures differ from this plan, update this plan before writing code; do not force a stale plan over user changes.

## Scope And File Map

| Path | Change | Responsibility |
| --- | --- | --- |
| `src/code_agent/core/AGENTS.md` | Modify | Record task lifecycle, supervisor, and task-aware dispatch Units. |
| `src/code_agent/core/task.py` | Create | Immutable task contract, authorization, status, snapshot, limits, transition validation, and task control values. |
| `src/code_agent/core/task_supervisor.py` | Create | Pure bounded budget/stall/checkpoint decisions from task facts and action results. |
| `src/code_agent/core/events.py` | Modify | Add durable task lifecycle event kinds. |
| `src/code_agent/core/limits.py` | Modify | Extend persistent budget values to include cumulative usage and repair/stall counters. |
| `src/code_agent/core/protocols.py` | Modify | Add task repository/control protocols and pass task authorization to action dispatch. |
| `src/code_agent/core/engine.py` | Modify | Run a supplied task, enforce durable limits, consume queued steering at safe boundaries, and emit task events. |
| `src/code_agent/core/tests/test_task.py` | Create | Validate immutable task models and legal lifecycle transitions. |
| `src/code_agent/core/tests/test_task_supervisor.py` | Create | Validate deterministic budget, retry, and repeated-failure pause decisions. |
| `src/code_agent/core/tests/test_engine_run.py` | Modify | Cover task-aware execution, pause, interruption, steering, and durable counters. |
| `src/code_agent/sessions/AGENTS.md` | Modify | Document task aggregate, checkpoint, and durable budget persistence responsibilities. |
| `src/code_agent/sessions/_database.py` | Modify | Add schema v5 task table and cumulative budget columns with migration validation. |
| `src/code_agent/sessions/_codec.py` | Modify | Encode/decode task records and extended task budgets. |
| `src/code_agent/sessions/repository.py` | Modify | Persist/load/transition/list task records, controls, checkpoints, and durable budget usage atomically. |
| `src/code_agent/sessions/tests/test_migrations.py` | Modify | Prove v4 databases migrate without losing existing state. |
| `src/code_agent/sessions/tests/test_repository.py` | Modify | Prove task persistence, resume safety data, and atomic reservations. |
| `src/code_agent/policy/AGENTS.md` | Modify | Document task-scoped local authorization without weakening hard boundaries. |
| `src/code_agent/policy/models.py` | Modify | Carry a typed `TaskAuthorization`-aware policy decision input. |
| `src/code_agent/policy/engine.py` | Modify | Allow ordinary workspace write/execute only under a valid task authorization. |
| `src/code_agent/policy/tests/test_engine.py` | Modify | Prove task authorization behavior and legacy behavior without a task grant. |
| `src/code_agent/policy/tests/test_command_security.py` | Modify | Prove network, outside-workspace, and critical commands remain blocked or require a decision. |
| `src/code_agent/interfaces/AGENTS.md` | Modify | Record foreground task controls, localization, and task-state projection Units. |
| `src/code_agent/interfaces/task_controller.py` | Create | UI-facing foreground task facade; starts, resumes, pauses, stops, and queues steering without knowing SQLite details. |
| `src/code_agent/interfaces/tui_commands.py` | Create | Pure parser for TUI slash commands, separate from process-level CLI grammar. |
| `src/code_agent/interfaces/i18n.py` | Create | Chinese-default/English UI catalog and locale selection. |
| `src/code_agent/interfaces/controller.py` | Modify | Expose the task runner to interactive and JSON consumers without duplicating engine semantics. |
| `src/code_agent/interfaces/commands.py` | Modify | Add `agent task list` and `agent task resume <task-id>` process-level commands. |
| `src/code_agent/interfaces/terminal_state.py` | Modify | Project typed task events into a compact terminal view model. |
| `src/code_agent/interfaces/windows_tui.py` | Modify | Render task header/footer, route slash commands, queue steering, and turn exit into an interrupted task. |
| `src/code_agent/interfaces/tests/test_task_controller.py` | Create | Validate foreground task control facade and safe steering boundaries. |
| `src/code_agent/interfaces/tests/test_tui_commands.py` | Create | Validate slash command grammar. |
| `src/code_agent/interfaces/tests/test_i18n.py` | Create | Validate Chinese Windows default and explicit English selection. |
| `src/code_agent/interfaces/tests/test_terminal_state.py` | Modify | Validate task-event projection and bilingual UI state. |
| `src/code_agent/interfaces/tests/test_windows_tui.py` | Modify | Validate TUI controls, rendering, and exit interruption. |
| `src/code_agent/interfaces/tests/test_commands.py` | Modify | Validate task list/resume command behavior. |
| `code_agent_win/app.py` | Modify in integration phase only | Assemble the task repository, task contract factory, task-aware dispatcher, controller, and TUI. |
| `code_agent_win/cli.py` | Modify in integration phase only | Preserve existing profile/model option behavior while routing task commands. |
| `tests/test_agent_app.py` | Modify in integration phase only | Exercise the complete foreground task loop in a temporary workspace. |
| `README.md` | Modify in integration phase only | Document task lifecycle, Chinese UI choice, controls, budgets, and explicit non-goals. |

## Phase 0: Baseline Gate

### Task 1: Freeze The Live Baseline Before Touching The Task Lifecycle

**Files:**
- Read: `git status --short`
- Read: `docs/superpowers/plans/2026-07-11-agent-context-budget-and-task-state.md`
- Read: `src/code_agent/core/task_state.py`, `src/code_agent/core/limits.py`, `src/code_agent/sessions/repository.py`
- Test: existing core/context/sessions/config and root integration suites

- [ ] **Step 1: Record the currently owned and concurrent changes.**

Run:

```powershell
git status --short
git diff -- src/code_agent/core src/code_agent/context src/code_agent/sessions src/code_agent/config code_agent_win
```

Expected: identify all pre-existing user changes. Do not revert, stage, amend, or reformat unrelated work.

- [ ] **Step 2: Install the package into the active development interpreter.**

Run:

```powershell
python -m pip install -e .
```

Expected: `httpx`, `psutil`, and `regex` resolve from `pyproject.toml`; no new global dependency is introduced for this feature.

- [ ] **Step 3: Run the existing task foundation tests before adding lifecycle code.**

Run:

```powershell
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: pass, or any existing failure is recorded as a pre-existing blocker before this plan begins. Do not attribute a baseline failure to the new task lifecycle.

- [ ] **Step 4: Resolve composition API drift as a prerequisite, not as part of task semantics.**

Verify that `code_agent_win.cli.run()` calls the current `create_application()` signature and that `tests/test_agent_app.py` imports only live symbols. If concurrent local API configuration work is unfinished, finish or merge that work first; do not introduce a second configuration path in this plan.

Expected: `python -m unittest tests.test_agent_app -v` reaches task setup without `TypeError` from stale `create_application` arguments or imports of absent symbols.

## Phase 1: Requirements Contracts

### Task 2: Extend Feature Contracts Before Unit Implementation

**Files:**
- Modify: `src/code_agent/core/AGENTS.md`
- Modify: `src/code_agent/sessions/AGENTS.md`
- Modify: `src/code_agent/policy/AGENTS.md`
- Modify: `src/code_agent/interfaces/AGENTS.md`

- [ ] **Step 1: Add the following core Units to `src/code_agent/core/AGENTS.md`.**

```markdown
- `TaskAuthorization`、`TaskContract`、`TaskRecord`、`TaskStatus`: 表达前台自主任务的范围、预算和生命周期 | 无副作用 | 终态不可恢复，网络、工作区外和 critical 能力不由普通任务授权
- `TaskSupervisor.observe(...)`: 根据持久预算、验证结果和失败指纹决定继续、checkpoint、暂停或等待决策 | 无副作用 | 只使用结构化工具结果，不从模型散文推断进度
- `AgentEngine.run(..., task=...)`: 在同一 thread 内执行一个显式任务并持久化任务事件 | 调用抽象模型、动作与会话协议 | 在安全边界消费 steering，绝不重放中断中的命令
```

- [ ] **Step 2: Add the following session Units.**

```markdown
- `SQLiteSessionRepository.create_task`、`load_task`、`transition_task`、`list_tasks`: 持久化任务契约和生命周期 | SQLite I/O | thread 是会话容器，task 是可恢复执行单元
- `consume_task_usage`、`record_task_control`: 原子累计 token/重试/失败签名并保存控制指令 | SQLite I/O | 恢复同一任务不能重置预算或丢失 steering
```

- [ ] **Step 3: Add the following policy and interface Units.**

```markdown
- `ActionPolicy.evaluate(request, task_authorization)`: 在有效任务授权下允许普通工作区写入和本地非网络命令 | 无副作用 | unknown、critical、network、outside-workspace 仍按硬边界处理
- `ForegroundTaskController`: 创建、附着、暂停、恢复、停止前台任务，并在安全边界提交 steering | 调用 core runner | 不持有 SQLite 或直接执行工具
- `parse_tui_command(text)`: 解析 TUI 内部 `/任务`、`/暂停`、`/继续`、`/停止`、`/引导`、`/语言` | 无副作用 | 不与进程级 CLI grammar 循环依赖
```

- [ ] **Step 4: Review the four contracts together.**

Expected: core owns pure lifecycle semantics; sessions owns persistence; policy owns permission evaluation; interfaces owns presentation and controls. `code_agent_win/` remains untouched until the integration phase.

## Phase 2: Core And Session Units

### Task 3: Add Typed Task Contract And Legal Lifecycle Transitions

**Files:**
- Create: `src/code_agent/core/task.py`
- Create: `src/code_agent/core/tests/test_task.py`
- Modify: `src/code_agent/core/events.py`

- [ ] **Step 1: Write failing lifecycle tests.**

Create tests for the exact properties below:

```python
def test_running_task_can_pause_and_resume() -> None:
    task = TaskRecord.new("thread-1", "repair provider tests", TaskAuthorization.local_workspace("C:/repo"))
    running = task.transition(TaskStatus.RUNNING)
    paused = running.transition(TaskStatus.PAUSED, "user requested pause")
    self.assertEqual(paused.transition(TaskStatus.RUNNING).status, TaskStatus.RUNNING)

def test_completed_task_cannot_resume() -> None:
    task = TaskRecord.new("thread-1", "inspect", TaskAuthorization.local_workspace("C:/repo"))
    completed = task.transition(TaskStatus.RUNNING).transition(TaskStatus.COMPLETED)
    with self.assertRaises(ValueError):
        completed.transition(TaskStatus.RUNNING)

def test_default_authorization_never_enables_network_or_outside_workspace() -> None:
    authorization = TaskAuthorization.local_workspace("C:/repo")
    self.assertTrue(authorization.allow_workspace_write)
    self.assertTrue(authorization.allow_local_execute)
    self.assertFalse(authorization.allow_network)
    self.assertFalse(authorization.allow_outside_workspace)
```

- [ ] **Step 2: Run the task-model test to prove it fails.**

Run:

```powershell
python -m unittest src.code_agent.core.tests.test_task -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'code_agent.core.task'`.

- [ ] **Step 3: Implement the immutable model in `core/task.py`.**

Define these public values:

```python
class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    VERIFYING = "verifying"
    WAITING_DECISION = "waiting_decision"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

@dataclass(frozen=True)
class TaskAuthorization:
    workspace_root: str
    allow_workspace_write: bool = True
    allow_local_execute: bool = True
    allow_network: bool = False
    allow_outside_workspace: bool = False

    @classmethod
    def local_workspace(cls, workspace_root: str) -> "TaskAuthorization": ...

@dataclass(frozen=True)
class TaskContract:
    objective: str
    authorization: TaskAuthorization
    max_active_seconds: int = 1_200
    max_repair_cycles: int = 3
    max_repeated_failure_signatures: int = 3

@dataclass(frozen=True)
class TaskRecord:
    id: str
    thread_id: str
    contract: TaskContract
    status: TaskStatus
    stop_reason: str | None
    created_at: datetime
    updated_at: datetime
```

Use `to_dict()`/`from_dict()` methods built from the existing JSON validation helpers. Permit only `CREATED -> RUNNING`, `RUNNING/VERIFYING -> PAUSED|WAITING_DECISION|COMPLETED|FAILED|INTERRUPTED`, and `PAUSED|INTERRUPTED|WAITING_DECISION -> RUNNING`; reject transitions from `COMPLETED` and `FAILED`.

- [ ] **Step 4: Add durable task event kinds.**

Extend `EventKind` with exactly:

```python
TASK_CREATED = "task_created"
TASK_STATUS_CHANGED = "task_status_changed"
TASK_CHECKPOINT_CREATED = "task_checkpoint_created"
TASK_BUDGET_WARNING = "task_budget_warning"
TASK_PAUSED = "task_paused"
TASK_DECISION_REQUIRED = "task_decision_required"
```

Task-event payloads must contain only task ID, status, reason, and bounded numeric counters or paths; never serialize prompt text, API secrets, raw command output, or a task authorization outside its safe fields.

- [ ] **Step 5: Run task and core event tests.**

Run:

```powershell
python -m unittest src.code_agent.core.tests.test_task -v
python -m unittest src.code_agent.core.tests.test_events -v
```

Expected: PASS.

- [ ] **Step 6: Commit the isolated core model slice.**

```powershell
git add src/code_agent/core/AGENTS.md src/code_agent/core/task.py src/code_agent/core/events.py src/code_agent/core/tests/test_task.py
git commit -m "feat: add foreground task lifecycle models"
```

### Task 4: Persist Task Records And Cumulative Budget State

**Files:**
- Modify: `src/code_agent/sessions/_database.py`
- Modify: `src/code_agent/sessions/_codec.py`
- Modify: `src/code_agent/sessions/repository.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/limits.py`
- Modify: `src/code_agent/sessions/tests/test_migrations.py`
- Modify: `src/code_agent/sessions/tests/test_repository.py`

- [ ] **Step 1: Write failing persistence tests.**

Add these cases:

```python
async def test_task_survives_repository_reopen(self) -> None:
    task = await repository.create_task("thread-1", contract)
    await repository.transition_task(task.id, TaskStatus.RUNNING)
    reopened = SQLiteSessionRepository(self.database_path)
    loaded = await reopened.load_task(task.id)
    self.assertEqual(loaded.status, TaskStatus.RUNNING)
    self.assertEqual(loaded.contract.authorization.workspace_root, str(self.root))

async def test_cumulative_usage_does_not_reset_after_resume(self) -> None:
    task = await repository.create_task("thread-1", contract)
    await repository.consume_task_usage(task.id, Usage(input_tokens=40, output_tokens=10))
    reopened = SQLiteSessionRepository(self.database_path)
    budget = await reopened.load_task_budget(task.id)
    self.assertEqual(budget.input_tokens, 40)
    self.assertEqual(budget.output_tokens, 10)
```

Also add a migration fixture at schema v4 containing `task_budgets` and `task_states`; after opening, assert those rows remain readable and the new task table exists.

- [ ] **Step 2: Run session tests to prove the APIs are absent.**

Run:

```powershell
python -m unittest src.code_agent.sessions.tests.test_migrations -v
python -m unittest src.code_agent.sessions.tests.test_repository -v
```

Expected: FAIL because `create_task`, `load_task`, and `consume_task_usage` do not exist.

- [ ] **Step 3: Add schema v5 without altering old rows.**

Increment `SCHEMA_VERSION` to 5 and add a migration equivalent to:

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id) ON DELETE CASCADE,
  contract TEXT NOT NULL,
  status TEXT NOT NULL,
  stop_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX tasks_status_updated ON tasks(status, updated_at DESC, id);
ALTER TABLE task_budgets ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_budgets ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_budgets ADD COLUMN repair_cycles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_budgets ADD COLUMN repeated_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_budgets ADD COLUMN last_failure_signature TEXT;
```

Extend `_REQUIRED_COLUMNS` for every new column. Validate non-negative counters and ensure `last_failure_signature` is bounded before it reaches SQLite.

- [ ] **Step 4: Add repository and protocol methods.**

Extend `SessionRepository` and `SQLiteSessionRepository` with:

```python
async def create_task(self, thread_id: str, contract: TaskContract) -> TaskRecord: ...
async def load_task(self, task_id: str) -> TaskRecord: ...
async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None: ...
async def transition_task(self, task_id: str, status: TaskStatus, reason: str | None = None) -> TaskRecord: ...
async def list_tasks(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]: ...
async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget: ...
async def observe_task_validation(self, task_id: str, fingerprint: str | None, changed_files: int) -> TaskBudget: ...
```

`transition_task` must load, validate, and update a `TaskRecord` in one write transaction. `consume_task_usage` must reject a cumulative total above `EngineLimits.max_total_tokens`; it must never silently wrap or reset counters after reopening the database.

- [ ] **Step 5: Create meaningful checkpoints from existing session storage.**

Use existing `create_checkpoint(thread_id, label, metadata)`; do not add filesystem rollback. Store a bounded metadata mapping with `task_id`, `status`, `reason`, changed paths, current budget counters, and verified facts. Create checkpoints only at task creation, before the first write, after a failed validation following changes, pause/wait-decision, interruption, and completion.

- [ ] **Step 6: Run session and compatibility suites.**

Run:

```powershell
python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: PASS, including schema v1-v4 migration coverage and existing task-state/budget tests.

- [ ] **Step 7: Commit the persistence slice.**

```powershell
git add src/code_agent/sessions src/code_agent/core/protocols.py src/code_agent/core/limits.py
git commit -m "feat: persist foreground task records and usage"
```

### Task 5: Add Deterministic Budget And No-Progress Supervision

**Files:**
- Create: `src/code_agent/core/task_supervisor.py`
- Create: `src/code_agent/core/tests/test_task_supervisor.py`
- Modify: `src/code_agent/core/limits.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/tests/test_engine_limits.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Write failing supervisor tests.**

Cover exact deterministic behavior:

```python
def test_same_validation_failure_three_times_pauses_task() -> None:
    supervisor = TaskSupervisor(contract)
    for _ in range(2):
        self.assertEqual(supervisor.observe_validation("pytest:1:abc", changed_files=1).kind, SupervisionKind.CONTINUE)
    decision = supervisor.observe_validation("pytest:1:abc", changed_files=1)
    self.assertEqual(decision.kind, SupervisionKind.PAUSE)
    self.assertEqual(decision.reason, "repeated validation failure")

def test_new_failure_signature_resets_repetition_counter() -> None:
    supervisor = TaskSupervisor(contract)
    supervisor.observe_validation("pytest:1:abc", changed_files=1)
    decision = supervisor.observe_validation("pytest:1:def", changed_files=1)
    self.assertEqual(decision.kind, SupervisionKind.CONTINUE)

def test_expired_task_stops_before_next_model_turn() -> None:
    expired = replace(contract, max_active_seconds=1)
    self.assertEqual(TaskSupervisor(expired, started_at=past).before_model_turn().kind, SupervisionKind.PAUSE)
```

- [ ] **Step 2: Run the new tests to prove the module is absent.**

Run:

```powershell
python -m unittest src.code_agent.core.tests.test_task_supervisor -v
```

Expected: FAIL with `ModuleNotFoundError` for `code_agent.core.task_supervisor`.

- [ ] **Step 3: Implement only structured supervision.**

Define:

```python
class SupervisionKind(str, Enum):
    CONTINUE = "continue"
    WARN = "warn"
    PAUSE = "pause"

@dataclass(frozen=True)
class SupervisionDecision:
    kind: SupervisionKind
    reason: str | None = None

class TaskSupervisor:
    def before_model_turn(self) -> SupervisionDecision: ...
    def before_external_action(self) -> SupervisionDecision: ...
    def observe_validation(self, fingerprint: str | None, changed_files: int) -> SupervisionDecision: ...
```

Generate a failure fingerprint only from `run_command` action result fields: normalized command label, return code, termination reason, and a 256-character normalized stdout/stderr prefix. Never use provider text, chain-of-thought, or arbitrary similarity. Repeated-failure pause requires the same non-empty fingerprint after a workspace change; generic read failures must not trigger it.

- [ ] **Step 4: Wire supervision into `AgentEngine`.**

Before a model turn and before dispatching a write/execute action, request a `SupervisionDecision`. On `WARN`, emit `TASK_BUDGET_WARNING`; on `PAUSE`, create a checkpoint, transition the task to `PAUSED`, append `TASK_PAUSED`, and return from the generator without invoking another model turn or tool. After each `run_command`, persist validation observation before the next model call.

Use the existing `CancellationToken` only for explicit user cancellation; a budget/stall pause is a normal terminal state, not `CANCELLED` or `ERROR`.

- [ ] **Step 5: Add engine-level regression tests.**

Use a fake model with a write -> failing `pytest` -> repeated write/test cycle. Assert the third identical failure emits `TASK_PAUSED`, creates a checkpoint, and leaves one fake-model stream unused. Add a resume test that creates a fresh engine/repository instance and asserts cumulative model turns and tokens remain bounded.

- [ ] **Step 6: Run all core tests.**

Run:

```powershell
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 7: Commit the supervisor slice.**

```powershell
git add src/code_agent/core
git commit -m "feat: pause foreground tasks on budget and stalls"
```

## Phase 2: Task-Scoped Authorization

### Task 6: Permit Ordinary Local Work Inside An Explicit Task Contract

**Files:**
- Modify: `src/code_agent/policy/models.py`
- Modify: `src/code_agent/policy/engine.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/policy/tests/test_engine.py`
- Modify: `src/code_agent/policy/tests/test_command_security.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Write failing policy tests.**

Add tests with `TaskAuthorization.local_workspace(root)`:

```python
def test_task_grant_allows_workspace_write_without_action_prompt(self) -> None:
    decision = self.policy.evaluate(write_request("src/a.py"), self.grant)
    self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)

def test_task_grant_allows_non_network_local_test_command(self) -> None:
    decision = self.policy.evaluate(command_request("python -m unittest"), self.grant)
    self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)

def test_task_grant_does_not_allow_network_or_outside_workspace(self) -> None:
    self.assertNotEqual(self.policy.evaluate(command_request("pip install thing"), self.grant).outcome, DecisionOutcome.ALLOW)
    self.assertNotEqual(self.policy.evaluate(write_request("..\\outside.py"), self.grant).outcome, DecisionOutcome.ALLOW)
```

Keep existing tests that call `evaluate(request)` without a grant; their legacy `plan`/`ask`/`auto` behavior must remain unchanged.

- [ ] **Step 2: Run policy tests to prove task authorization is not implemented.**

Run:

```powershell
python -m unittest discover -s src/code_agent/policy/tests -p 'test_*.py' -v
```

Expected: FAIL because `ActionPolicy.evaluate` does not accept an authorization and local commands still request approval.

- [ ] **Step 3: Extend the policy API without granting a blanket shell.**

Use this signature:

```python
def evaluate(
    self,
    request: ActionRequest,
    task_authorization: TaskAuthorization | None = None,
) -> PolicyDecision:
```

When a valid authorization root matches `PolicyConfig.workspace_root`, return `ALLOW` only for:

- workspace read/write typed tools;
- `run_command` with `EXECUTE` but without `NETWORK` or `OUTSIDE_WORKSPACE` capability;
- non-critical commands.

Always preserve `DENY` for unknown/critical actions. Return `ASK` for network/outside-workspace actions rather than silently granting them. `PLAN` mode remains read-only even when a task contract exists.

- [ ] **Step 4: Pass the contract down the core dispatch protocol.**

Change the protocol and engine call site to:

```python
async def dispatch(
    self,
    request: ActionRequest,
    cancellation: CancellationToken,
    task_authorization: TaskAuthorization | None = None,
) -> ActionResult: ...
```

The engine supplies the active task authorization; fake dispatchers in tests accept the optional argument. Do not put task authorization inside model-generated `ToolCall.arguments`.

- [ ] **Step 5: Run policy and core suites.**

Run:

```powershell
python -m unittest discover -s src/code_agent/policy/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: PASS. The suite must show no approval wait for an authorized local write/test and no accidental permission for network, outside workspace, unknown, or critical actions.

- [ ] **Step 6: Commit the authorization slice.**

```powershell
git add src/code_agent/policy src/code_agent/core
git commit -m "feat: authorize ordinary local task actions"
```

## Phase 2: Foreground Control And Chinese TUI Units

### Task 7: Add A Foreground Task Controller And Safe Steering Queue

**Files:**
- Create: `src/code_agent/interfaces/task_controller.py`
- Create: `src/code_agent/interfaces/tests/test_task_controller.py`
- Modify: `src/code_agent/interfaces/controller.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Write failing foreground-control tests.**

Cover this sequence:

```python
async def test_pause_cancels_current_run_and_persists_paused_task(self) -> None:
    task = await controller.start("repair tests")
    await controller.pause(task.id)
    self.assertEqual((await repository.load_task(task.id)).status, TaskStatus.PAUSED)

async def test_steering_is_consumed_only_between_tool_boundaries(self) -> None:
    task = await controller.start("repair tests")
    await controller.steer(task.id, "do not edit public API")
    await fake_dispatcher.release_current_tool()
    self.assertEqual(fake_model.second_turn_messages[-1].content, "do not edit public API")

async def test_resume_uses_same_task_and_never_replays_inflight_command(self) -> None:
    task = interrupted_task_with_command_checkpoint()
    await controller.resume(task.id, "continue safely")
    self.assertEqual(fake_runtime.commands, ["python -m unittest"])
```

- [ ] **Step 2: Run the controller test to prove it fails.**

Run:

```powershell
python -m unittest src.code_agent.interfaces.tests.test_task_controller -v
```

Expected: FAIL with `ModuleNotFoundError` for `code_agent.interfaces.task_controller`.

- [ ] **Step 3: Implement a UI-facing facade, not a second engine.**

Define a `ForegroundTaskController` that receives an `AgentController`, `SessionRepository`, a workspace-root contract factory, and a cancellation-token registry. Expose:

```python
async def start(self, prompt: str) -> TaskRecord: ...
async def events(self, task_id: str, prompt: str | None = None) -> AsyncIterator[AgentEvent]: ...
async def pause(self, task_id: str, reason: str = "user requested pause") -> None: ...
async def stop(self, task_id: str) -> None: ...
async def resume(self, task_id: str, instruction: str = "continue safely") -> AsyncIterator[AgentEvent]: ...
async def steer(self, task_id: str, instruction: str) -> None: ...
```

`steer` persists a bounded user message immediately. `AgentEngine` drains queued steering only after the active action completes and before beginning the next model turn. `/pause` cancels the token and transitions to `PAUSED`; `/stop` uses `FAILED` with reason `user stopped task`; a process/TUI exit uses `INTERRUPTED`.

- [ ] **Step 4: Ensure resume is safe.**

On resume, load the task contract, current `TaskState`, budgets, and latest checkpoint. Emit a task status event, add the explicit resume instruction as a new user message, and start a new model turn. Never replay a command that was running when the prior process stopped; the model sees the recorded interruption and chooses the next safe action.

- [ ] **Step 5: Run controller and core regression suites.**

Run:

```powershell
python -m unittest src.code_agent.interfaces.tests.test_task_controller -v
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 6: Commit the foreground-control slice.**

```powershell
git add src/code_agent/interfaces/task_controller.py src/code_agent/interfaces/tests/test_task_controller.py src/code_agent/interfaces/controller.py src/code_agent/core
git commit -m "feat: control foreground autonomous tasks"
```

### Task 8: Add TUI Slash Commands, Typed Task Projection, And Localization

**Files:**
- Create: `src/code_agent/interfaces/tui_commands.py`
- Create: `src/code_agent/interfaces/i18n.py`
- Create: `src/code_agent/interfaces/tests/test_tui_commands.py`
- Create: `src/code_agent/interfaces/tests/test_i18n.py`
- Modify: `src/code_agent/interfaces/terminal_state.py`
- Modify: `src/code_agent/interfaces/windows_tui.py`
- Modify: `src/code_agent/interfaces/tests/test_terminal_state.py`
- Modify: `src/code_agent/interfaces/tests/test_windows_tui.py`

- [ ] **Step 1: Write failing slash-command and locale tests.**

Add exact parser tests:

```python
self.assertEqual(parse_tui_command("/任务").kind, TuiCommandKind.TASKS)
self.assertEqual(parse_tui_command("/pause T-042").task_id, "T-042")
self.assertEqual(parse_tui_command("/引导 不要修改公开 API").instruction, "不要修改公开 API")
with self.assertRaises(ValueError):
    parse_tui_command("/继续")
```

Add locale tests:

```python
self.assertEqual(select_language("zh_CN", None), Language.ZH_CN)
self.assertEqual(select_language("en_US", None), Language.EN_US)
self.assertEqual(select_language("zh_CN", "en"), Language.EN_US)
```

Add renderer tests asserting Chinese default task text and explicit English output while preserving a path exactly:

```python
screen = render_terminal(state, "", 100, 30, catalog=ZH_CN)
self.assertIn("任务 T-042", screen)
self.assertIn("src/code_agent/core/engine.py", screen)
```

- [ ] **Step 2: Run new interface tests to prove the APIs are absent.**

Run:

```powershell
python -m unittest src.code_agent.interfaces.tests.test_tui_commands -v
python -m unittest src.code_agent.interfaces.tests.test_i18n -v
```

Expected: FAIL because `tui_commands.py` and `i18n.py` do not exist.

- [ ] **Step 3: Implement pure TUI command parsing.**

Do not import `WindowsTerminalApp` from the parser. Support Chinese and English aliases:

```python
class TuiCommandKind(str, Enum):
    TASKS = "tasks"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    STEER = "steer"
    DIFF = "diff"
    LANGUAGE = "language"

def parse_tui_command(text: str) -> TuiCommand | None: ...
```

Recognize `/任务` and `/tasks`; `/暂停` and `/pause`; `/继续` and `/resume`; `/停止` and `/stop`; `/引导` and `/steer`; `/差异` and `/diff`; `/语言` and `/language`. Return `None` for ordinary non-slash input. Require a task ID where the command cannot infer the currently attached task.

- [ ] **Step 4: Implement localization as a UI-only catalog.**

`i18n.py` must provide `Language`, `UiCatalog`, `select_language(system_locale, explicit)`, `ZH_CN`, and `EN_US`. Default `zh_CN`, `zh-CN`, and Chinese Windows locale prefixes to `ZH_CN`; all other/unknown locales default to `EN_US`. Read an optional `CODE_AGENT_LANGUAGE=auto|zh-CN|en` only at composition time; do not put language selection in model context, task events, or workspace files.

- [ ] **Step 5: Project task events in `TerminalState`.**

Add typed fields such as `task_id`, `task_status`, `task_objective`, `task_budget_line`, `checkpoint_label`, and `pending_decision`. Populate them only from `TASK_*` events. Do not store translated state strings in the model; rendering maps typed statuses through `UiCatalog`.

- [ ] **Step 6: Update `WindowsTerminalApp` without adding a dashboard.**

Keep the continuous transcript. Add a compact task header only while the attached task is non-terminal and a one-line footer such as:

```text
任务 T-042 · 正在验证 · 检查点 3 · Esc 暂停 · /任务 查看
```

When a task is active, `Esc` must call `ForegroundTaskController.pause`; `q`/TUI finalization must transition it to `INTERRUPTED` after cancelling the active runtime; a slash command routes through `ForegroundTaskController`; ordinary input while a task runs queues steering instead of being discarded. Render tool output as compact action lines and never render raw serialized JSON tool payloads.

- [ ] **Step 7: Run the interface suite.**

Run:

```powershell
python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
```

Expected: PASS, including previous approval/session/diff behavior.

- [ ] **Step 8: Commit the TUI slice.**

```powershell
git add src/code_agent/interfaces
git commit -m "feat: add Chinese foreground task controls"
```

## Phase 3: Integration Only

### Task 9: Compose The Foreground Task System In The Windows Application

**Files:**
- Modify: `code_agent_win/AGENTS.md`
- Modify: `code_agent_win/app.py`
- Modify: `code_agent_win/cli.py`
- Modify: `tests/test_agent_app.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing full-stack tests.**

Add a fake-model test that produces: read -> write -> failing `python -m unittest` -> revised write -> passing `python -m unittest` -> final text. Assert:

```python
self.assertEqual(task.status, TaskStatus.COMPLETED)
self.assertIn("note.txt", (await repository.load_task_state(task.thread_id)).files_changed)
self.assertGreaterEqual(len(await repository.list_checkpoints(task.thread_id)), 2)
self.assertEqual((await repository.load_task_budget(task.id)).repair_cycles, 1)
```

Add a hard-boundary fake-model test that requests `pip install package`; assert no runtime process starts, task becomes `WAITING_DECISION`, and the persisted event does not expose secrets.

- [ ] **Step 2: Run integration tests to prove composition has no task facade.**

Run:

```powershell
python -m unittest tests.test_agent_app -v
```

Expected: FAIL because `Application` does not expose a foreground task controller and the dispatcher does not receive task authorization.

- [ ] **Step 3: Wire components in `create_application` only.**

Compose one `SQLiteSessionRepository`, one `AgentEngine`, one `AgentController`, one `ForegroundTaskController`, and one `WindowsTerminalApp`. Pass the same workspace root into `TaskAuthorization.local_workspace(root)`. Preserve the current provider/config composition, including any completed local API profile support; do not reintroduce scattered `os.getenv` calls or modify provider behavior.

`RootActionDispatcher.dispatch` receives the optional authorization from the core protocol and calls `ActionPolicy.evaluate(request, task_authorization)`. It continues to create `WorkspaceEditor` plans and uses `WindowsLocalRuntime` for cancellation-safe commands; it must not acquire Git worktrees, create commits, or push.

- [ ] **Step 4: Add process-level task commands.**

Extend `parse_command`/`execute_command` with exactly:

```text
agent task list
agent task resume <task-id>
```

`task list` prints task ID, status, objective preview, and latest checkpoint with no provider call. `task resume` opens/attaches the same foreground task and requires an explicit resume instruction if the task is `WAITING_DECISION`. Preserve `agent`, `ask`, `resume`, `run --json`, `--profile`, and `--model` compatibility.

- [ ] **Step 5: Document the constrained product behavior.**

Update README with:

- Chinese Windows default and `CODE_AGENT_LANGUAGE`/TUI language command;
- foreground task lifecycle and controls;
- task-level local autonomy plus hard boundaries;
- budget/no-progress pauses and checkpoint recovery;
- explicit statement that closing the terminal, sleep, hibernate, shutdown, or reboot does not keep work running; the user resumes safely later;
- explicit statement that daemon, mobile observation, background continuation, worktrees, automatic commit, and push are not implemented in this phase.

- [ ] **Step 6: Run complete automated verification.**

Run:

```powershell
python -m unittest discover -s src/code_agent/core/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/context/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/policy/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/providers/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/runtime/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/sessions/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/workspace/tests -p 'test_*.py' -v
python -m unittest discover -s src/code_agent/interfaces/tests -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python -m build
```

Expected: all suites pass and `dist/` contains an installable wheel and source distribution. Resolve failures from pre-existing concurrent changes before claiming this phase complete.

- [ ] **Step 7: Run manual Windows Terminal acceptance checks.**

1. Start `agent` in a disposable workspace and submit a repair request.
2. Confirm a compact Chinese task header appears only when the task performs engineering work.
3. Confirm a workspace write and `python -m unittest` run without per-action approval.
4. Confirm `pip install`, an outside-workspace path, and a critical delete do not run automatically.
5. Confirm `/暂停`, `/继续`, `/停止`, `/任务`, `/引导`, `/语言 en`, and `Esc` have the documented behavior.
6. Exit the terminal during an active task, restart `agent`, and confirm the task appears as interrupted with a recoverable checkpoint; confirm resume does not replay the interrupted command.

- [ ] **Step 8: Commit only the completed integration slice.**

```powershell
git add code_agent_win tests README.md src/code_agent/interfaces/AGENTS.md
git commit -m "feat: add foreground autonomous task workflow"
```

## Completion Criteria

- A normal engineering request in the foreground terminal gets a durable task record, task-scoped workspace authorization, bounded execution, checkpoints, and a final evidence-backed state.
- Ordinary workspace writes and non-network local test commands do not trigger per-action approval under the task contract; hard boundaries never silently broaden.
- Token/model/tool budgets, active-time limit, repair-cycle count, and repeated validation failures survive a new process/repository instance and stop before another model/tool action.
- `Esc`, slash controls, and TUI exit produce the correct persistent task state; task recovery never replays an in-flight command.
- Chinese Windows defaults to Chinese UI; English selection changes only chrome and explanatory UI text, never code/path/command/tool data.
- No daemon, detached execution, mobile/remote observer, worktree, automatic commit, or push is added.
- Every automated command in Task 9 passes and the manual Windows Terminal checks succeed.

## Plan Self-Review

- **Coverage:** lifecycle, persistence, budget/stall safety, authorization, foreground controls, localization, integration, test and manual acceptance are mapped to Tasks 3-9.
- **No-overlap rule:** existing `TaskState`, task-budget, context-budget, and local API configuration work are explicit prerequisites and must be reused, not replaced.
- **Scope:** daemon, mobile, cloud, worktrees, automatic commit/push, and concurrency are excluded from all tasks.
- **Boundary discipline:** Phase 2 tasks modify only their corresponding feature directories; `code_agent_win/`, root tests, and README change only in Phase 3.
