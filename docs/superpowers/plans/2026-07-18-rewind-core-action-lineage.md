# Core Action Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Core 为每次实际 dispatcher 调用提供不可变、显式且可验证的 owner/origin/task/request/parent-request 执行归因。

**Architecture:** 新增独立的 `action_execution.py` 值对象模块，`AgentEngine` 只保存可选的父级 lineage，`AgentEngineActionMixin` 在 `ACTION_STARTED` 已持久化后为当前请求生成完整 execution context。现有 `models.py` 先做行为保持拆分以恢复 Python 文件粒度合规；公开导入继续由 `code_agent.core.models` facade 提供。

**Tech Stack:** Python 3.10+、`dataclasses`、`typing.Protocol`、`unittest`、异步生成器。

---

## Scope and invariants

- 本计划只实现 `src/code_agent/core/` Feature；不修改 `code_agent_win/`、其他 Feature 或根级集成文件。
- root engine：`owner_thread_id == origin_thread_id == 当前 thread_id`，`task_id` 取当前 `TaskRecord.id`（若无任务则为 `None`），`parent_request_id=None`。
- child engine：`origin_thread_id` 始终取当前 child thread；owner/task/parent request 原样继承构造器收到的 `ActionLineage`。
- 每个存在的 identifier 必须是非空白、最多 256 字符的字符串；模型不可变。
- unavailable tool 和 supervisor 在 dispatch 前暂停的路径不得构造或发送 execution context，也不得调用 dispatcher。
- context 只能在 `ACTION_STARTED` 成功持久化之后构造并传入 dispatcher。
- Core 不写 mutation journal、不创建 workspace snapshot，也不判断 checkpoint rewind coverage。
- `ActionDispatcher` 的新参数是 keyword-only；不得用 `TypeError` retry 兼容旧 dispatcher，因为那会产生双重执行风险。
- Core Feature 完成后，尚未适配的根级 dispatcher 会在后续 Integration 任务中接线；本阶段只以 Core Feature 全量测试为通过门槛。
- 所有 Core 源码和测试文件不超过 300 行，函数不超过 50 行。

## File map

- Create `src/code_agent/core/_action_models.py`: `ActionRequest`、`ActionResult`、`ToolDefinition` 的私有实现单元。
- Modify `src/code_agent/core/models.py`: 保持公开 facade，并移除已经迁出的重复定义。
- Create `src/code_agent/core/_engine_run.py`: 单次 run 生命周期、终态错误映射和 turn 循环。
- Create `src/code_agent/core/_engine_turn.py`: 模型回合准备、流处理、完成与动作批次编排。
- Create `src/code_agent/core/action_execution.py`: 不可变 action lineage/context 值对象。
- Modify `src/code_agent/core/protocols.py`: 为 dispatcher 增加 keyword-only execution context。
- Modify `src/code_agent/core/engine.py`: 接收、验证并保存可选父 lineage。
- Modify `src/code_agent/core/engine_actions.py`: 在已持久化 action-start 后生成并传递 context。
- Create `src/code_agent/core/tests/test_action_execution.py`: 值对象验证和冻结测试。
- Create `src/code_agent/core/tests/test_engine_action_lineage.py`: root/task/child 的 engine 传播测试。
- Modify `src/code_agent/core/tests/_engine_support.py`: fake dispatcher 记录 authorization/context。
- Modify `src/code_agent/core/tests/test_models.py`: 将既有超长 round-trip characterization test 按职责拆分。
- Modify `src/code_agent/core/tests/test_engine_tools.py`: 现有 ad-hoc root 和 unavailable 路径的短断言。
- Modify `src/code_agent/core/tests/test_protocols.py`: protocol fake 接受并记录新 keyword。
- Modify `src/code_agent/core/AGENTS.md`: 只在实现完成后补充 Units 契约。

---

### Task 0A: Split oversized action models and their long characterization test

**Files:**

- Create: `src/code_agent/core/_action_models.py`
- Modify: `src/code_agent/core/models.py`
- Modify: `src/code_agent/core/tests/test_models.py`

- [ ] **Step 1: Freeze the current model behavior**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_models -v
```

Expected: the existing 13 model tests pass before the move. If they do not, record the baseline failure and stop; do not hide it in the split.

- [ ] **Step 2: Create the private action-model module**

Create `_action_models.py` with the existing behavior copied exactly:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, cast

from ._json import (
    JSONValue,
    freeze_json,
    freeze_mapping,
    plain,
    validate_identifier,
    validate_json,
    validate_json_mapping,
    validate_name,
)


@dataclass(frozen=True)
class ActionRequest:
    id: str
    name: str
    arguments: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        validate_identifier(self.id, "id")
        validate_name(self.name)
        validate_json_mapping(self.arguments, "arguments")
        object.__setattr__(
            self, "arguments", freeze_mapping(self.arguments, "arguments")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": plain(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ActionRequest:
        return cls(
            id=cast(str, data["id"]),
            name=cast(str, data["name"]),
            arguments=cast(Mapping[str, JSONValue], data["arguments"]),
        )


@dataclass(frozen=True)
class ActionResult:
    request_id: str
    name: str
    output: JSONValue
    is_error: bool = False
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_name(self.name)
        validate_json(self.output, "output")
        if not isinstance(self.is_error, bool):
            raise TypeError("is_error must be a bool")
        validate_json_mapping(self.metadata, "metadata")
        object.__setattr__(self, "output", freeze_json(self.output, "output"))
        object.__setattr__(
            self, "metadata", freeze_mapping(self.metadata, "metadata")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "request_id": self.request_id,
            "name": self.name,
            "output": plain(self.output),
            "is_error": self.is_error,
            "metadata": plain(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ActionResult:
        return cls(
            request_id=cast(str, data["request_id"]),
            name=cast(str, data["name"]),
            output=cast(JSONValue, data["output"]),
            is_error=cast(bool, data.get("is_error", False)),
            metadata=cast(Mapping[str, JSONValue], data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        validate_name(self.name)
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        validate_json_mapping(self.parameters, "parameters")
        object.__setattr__(
            self, "parameters", freeze_mapping(self.parameters, "parameters")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": plain(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ToolDefinition:
        return cls(
            name=cast(str, data["name"]),
            description=cast(str, data["description"]),
            parameters=cast(Mapping[str, JSONValue], data["parameters"]),
        )
```

- [ ] **Step 3: Turn `models.py` into a compatible facade**

Delete the three original class bodies from `models.py`, import them under the same public names, and remove `_json` imports now used only by the private module:

```python
from ._action_models import ActionRequest, ActionResult, ToolDefinition
from ._json import (
    JSONValue,
    freeze_mapping,
    plain,
    validate_identifier,
    validate_json_mapping,
    validate_name,
)
```

Keep this existing re-export unchanged even though the names are not used inside the file:

```python
from .task_state import CommandFact, TaskState, TaskStateUpdate
```

Do not rewrite any caller to import from `_action_models`. Ordinary imports, class identity through the facade, validation, equality and JSON round-trip must remain compatible. The private definition module may become the new `__module__`; the repository has no pickle or exact-module contract, so do not mutate `__module__` at runtime.

- [ ] **Step 4: Split the existing 52-line round-trip test by model responsibility**

Keep `ModelRoundTripTests.assert_round_trip` unchanged. Replace
`test_all_models_round_trip_through_json_compatible_dicts` with three tests:

```python
def test_conversation_models_round_trip(self) -> None:
    tool_call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "README.md", "lines": [1, 20]},
    )
    usage = Usage(input_tokens=12, output_tokens=5, cached_input_tokens=3)
    message = Message(
        role="assistant", content="", name="coder", tool_calls=(tool_call,)
    )
    bundle = ContextBundle(system_prompt="Be precise.", messages=(message,))

    for value in (tool_call, usage, message, bundle):
        with self.subTest(model=type(value).__name__, value=value):
            self.assert_round_trip(value, type(value))

    self.assertEqual(usage.total_tokens, 17)

def test_action_models_round_trip(self) -> None:
    values = (
        ActionRequest(
            id="action-1",
            name="read_file",
            arguments={"path": "README.md"},
        ),
        ActionResult(
            request_id="action-1",
            name="read_file",
            output={"text": "hello", "truncated": False},
            metadata={"duration_ms": 2.5},
        ),
        ToolDefinition(
            name="read_file",
            description="Read a text file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
    )

    for value in values:
        with self.subTest(model=type(value).__name__, value=value):
            self.assert_round_trip(value, type(value))

def test_model_events_round_trip(self) -> None:
    tool_call = ToolCall("call-1", "read_file", {})
    usage = Usage(input_tokens=12, output_tokens=5, cached_input_tokens=3)
    values = (
        ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="hello"),
        ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=tool_call),
        ModelEvent(kind=ModelEventKind.USAGE, usage=usage),
        ModelEvent(kind=ModelEventKind.COMPLETED),
    )

    for value in values:
        with self.subTest(kind=value.kind):
            self.assert_round_trip(value, ModelEvent)
```

Each test is below 50 lines and preserves every value from the original test.

- [ ] **Step 5: Verify behavior and structural limits**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_models -v
& $python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/core
git diff --check
```

Expected: the expanded model tests and all Core tests pass; `models.py` and
`_action_models.py` are each at most 300 lines, and no model test function is
over 50 lines.

- [ ] **Step 6: Commit the behavior-preserving split**

```powershell
git add src/code_agent/core/_action_models.py `
  src/code_agent/core/models.py `
  src/code_agent/core/tests/test_models.py
git commit -m "拆分核心动作模型：恢复文件粒度合规"
```

---

### Task 0B: Split the oversized engine run loop by persistence phase

**Files:**

- Create: `src/code_agent/core/_engine_run.py`
- Create: `src/code_agent/core/_engine_turn.py`
- Modify: `src/code_agent/core/engine.py`
- Test: `src/code_agent/core/tests/test_engine_run.py`
- Test: `src/code_agent/core/tests/test_engine_limits.py`
- Test: `src/code_agent/core/tests/test_engine_tools.py`

The current inherited public behavior stays `AgentEngine.run(...)`, but its
254-line implementation must not be moved wholesale into another long
function.

- [ ] **Step 1: Re-run engine characterization tests before moving code**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_engine_run `
  src.code_agent.core.tests.test_engine_limits `
  src.code_agent.core.tests.test_engine_tools -v
```

Expected: all existing engine tests pass. Preserve their exact event order,
message persistence, error mapping, budget accounting and stop behavior.

- [ ] **Step 2: Introduce explicit mutable run/turn state owned by one run**

In `_engine_run.py` define:

```python
@dataclass
class _RunState:
    thread_id: str
    token: CancellationToken
    task: TaskRecord | None
    budget: TaskBudget
    supervisor: TaskSupervisor | None
    prior_messages: tuple[Message, ...] = ()
    messages: tuple[Message, ...] = ()
    used_call_ids: set[str] = field(default_factory=set)
    total_usage: Usage = field(default_factory=Usage)
    stop_requested: bool = False

@dataclass
class _TurnState:
    number: int
    tools: tuple[ToolDefinition, ...]
    tool_names: set[str]
    text_parts: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
```

Both dataclasses live in `_engine_run.py`; `_engine_turn.py` imports them.
These are private, per-run objects. Do not store them on `AgentEngine`; two
concurrent calls must not share message, usage, call-ID or stop state.

- [ ] **Step 3: Move lifecycle orchestration into small run helpers**

Create `AgentEngineRunMixin` in `_engine_run.py`. It owns private lifecycle
helpers; the public `run` method remains visibly defined on `AgentEngine`.
Move existing statements without changing their order into these exact helpers:

| Helper | Existing responsibility | Required result |
|---|---|---|
| `_validate_run_arguments(user_input, thread_id)` | `engine.py:55-61` | raises before thread creation |
| `_start_run(thread_id, cancellation, task)` | `:63-75` | `_RunState` plus already-persisted `RUN_STARTED` |
| `_prepare_request(state, user_input)` | `:79-90` | returns persisted `MESSAGE_ADDED` and user `Message`; caller updates messages after yielding |
| `_build_turn_context(state, turn, user_input)` | `:121-142` | wraps non-cancellation failures as `ContextBuildError` |
| `_stream_model_events(state, turn, bundle)` | `:158-204` | live-yields persisted model/warning events and validates completion |
| `_record_model_usage(state, usage)` | `:186-198` | writes cumulative usage back to state and live-yields warnings |

Every listed helper must be at most 50 lines. Preserve the important timing
that `RUN_STARTED` is yielded before cancellation, history, verification
prepare or user-message failures are processed. The public `run` in Step 5
retains the loop, terminal exception mapping and the post-yield message update.

- [ ] **Step 4: Split one model turn into bounded phase helpers**

Create `AgentEngineTurnMixin` in `_engine_turn.py`. Move the turn body into
these exact helpers:

| Helper | Existing responsibility | Boundary |
|---|---|---|
| `_run_turn(state, number, user_input)` | `engine.py:94-283` orchestration | live-yields one turn and sets `stop_requested` only on terminal paths |
| `_before_model_turn(state)` | `:95-107` | returns persisted pause event or `None` |
| `_start_turn(state, number)` | `:108-120` | reserves model turn, advertises tools, persists `TURN_STARTED` |
| `_finish_without_calls(state, turn)` | root branch `:212-246` | persists root completion or delegates task completion |
| `_finish_task_without_calls(state, turn)` | task branch `:213-242` | preserves automatic-verification RUNNING continuation |
| `_persist_completion_events(state, task)` | repeated `:229-241` | live-yields each event immediately after append |
| `_reserve_tool_calls(state, turn)` | `:248-261` | reserves entire batch and validates IDs before dispatch |
| `_dispatch_tool_calls(state, turn)` | `:263-283` | live-yields actions, updates messages, sets stop on pause/decision |

Required phase order:

```text
supervise/reserve model turn
→ persist TURN_STARTED
→ build context
→ persist CONTEXT_BUILT
→ persist MODEL_STARTED
→ stream/persist model events and warnings
→ persist assistant message
→ either finish text turn or reserve/dispatch the complete call batch
```

`_stream_model_events` must continue yielding events as they arrive; it must not
buffer the model stream into a tuple/list. `_dispatch_tool_calls` must continue
using `_dispatch` for normal calls; `_finish_task_without_calls` keeps calling
`_run_suggested_verification`, which in turn uses that same `_dispatch` path.
Every helper must be at most 50 lines.

- [ ] **Step 5: Reduce `engine.py` to construction and mixin composition**

Import the two mixins, compose them, and retain the public async generator
exactly as shown in the complete `engine.py` target below.

Delete the old `run` body and imports now owned only by the new modules.
`engine.py` must retain the same constructor signature and assignments at this
stage. The new public `run` is below 50 lines; the file should finish well below
150 lines, leaving safe room for
`action_lineage` in Task 2.

Use this complete target for `_engine_run.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .cancellation import CancellationError, CancellationToken
from .context_request import ContextRequest, budget_lease
from .errors import (
    AgentEngineError,
    ContextBuildError,
    EngineLimitError,
    ModelStreamError,
)
from .events import AgentEvent, EventKind
from .limits import TaskBudget, add_usage
from .models import (
    ContextBundle,
    Message,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)
from .task import TaskRecord
from .task_supervisor import TaskSupervisor


@dataclass
class _RunState:
    thread_id: str
    token: CancellationToken
    task: TaskRecord | None
    budget: TaskBudget
    supervisor: TaskSupervisor | None
    prior_messages: tuple[Message, ...] = ()
    messages: tuple[Message, ...] = ()
    used_call_ids: set[str] = field(default_factory=set)
    total_usage: Usage = field(default_factory=Usage)
    stop_requested: bool = False


@dataclass
class _TurnState:
    number: int
    tools: tuple[ToolDefinition, ...]
    tool_names: set[str]
    text_parts: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)


def _validate_run_arguments(user_input: object, thread_id: object) -> None:
    if not isinstance(user_input, str):
        raise TypeError("user_input must be a string")
    if not user_input.strip():
        raise ValueError("user_input must not be blank")
    if thread_id is not None and (
        not isinstance(thread_id, str) or not thread_id.strip()
    ):
        raise ValueError("thread_id must be a non-blank string or None")


class AgentEngineRunMixin:
    """Internal setup, context, and model-stream steps for one engine run."""

    async def _start_run(
        self,
        thread_id: str | None,
        cancellation: CancellationToken | None,
        task: TaskRecord | None,
    ) -> tuple[_RunState, AgentEvent]:
        token = cancellation or CancellationToken()
        active_thread = thread_id or await self._journal.create_thread()
        if task is not None and task.thread_id != active_thread:
            raise ValueError("task must belong to the active thread")
        budget = await self._journal.get_or_create_task_budget(
            active_thread, self._model_name, self._limits
        )
        supervisor = TaskSupervisor(task.contract, budget) if task else None
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        state = _RunState(active_thread, token, task, budget, supervisor)
        return state, started

    async def _prepare_request(
        self, state: _RunState, user_input: str
    ) -> tuple[AgentEvent, Message]:
        state.token.raise_if_cancelled()
        state.prior_messages = await self._journal.load_messages(
            state.thread_id
        )
        if state.task is not None and self._verification is not None:
            prepared = await self._verification.prepare(
                state.task,
                await self._journal.load_task_state(state.thread_id),
            )
            await self._journal.save_task_state(state.thread_id, prepared)
        user_message = Message(role="user", content=user_input)
        await self._journal.append_message(state.thread_id, user_message)
        added = self._journal.message_added(user_message)
        await self._journal.append_event(state.thread_id, added)
        return added, user_message

    async def _build_turn_context(
        self, state: _RunState, turn: _TurnState, user_input: str
    ) -> ContextBundle:
        source_messages = (
            await self._journal.load_messages(state.thread_id)
            if state.task is not None
            else state.prior_messages if turn.number == 1 else state.messages
        )
        source_input = user_input if turn.number == 1 else ""
        try:
            task_state = await self._journal.load_task_state(state.thread_id)
            bundle = await self._context.build(
                ContextRequest(
                    thread_id=state.thread_id,
                    revision=state.budget.model_turns,
                    messages=source_messages,
                    user_input=source_input,
                    tools=turn.tools,
                    task_state=task_state,
                    cancellation=state.token,
                    mode_snapshot=self._context_mode_snapshot,
                    permission_snapshot=self._context_permission_snapshot,
                    budget_lease=budget_lease(state.budget),
                )
            )
            if not isinstance(bundle, ContextBundle):
                raise TypeError("context builder returned an invalid bundle")
            return bundle
        except CancellationError:
            raise
        except Exception:
            raise ContextBuildError("context build failed") from None

    async def _stream_model_events(
        self, state: _RunState, turn: _TurnState, bundle: ContextBundle
    ) -> AsyncIterator[AgentEvent]:
        completed = False
        try:
            stream = self._model.stream(
                bundle.system_prompt,
                bundle.messages,
                turn.tools,
            )
            async for model_event in stream:
                state.token.raise_if_cancelled()
                if completed:
                    raise ModelStreamError(
                        "model emitted an event after completion"
                    )
                self._accumulate_model_event(
                    model_event, turn.text_parts, turn.calls
                )
                if model_event.kind is ModelEventKind.COMPLETED:
                    completed = True
                streamed = AgentEvent(
                    kind=EventKind.MODEL_EVENT,
                    payload={"event": model_event.to_dict()},
                )
                await self._journal.append_event(state.thread_id, streamed)
                yield streamed
                if model_event.usage is not None:
                    async for event in self._record_model_usage(
                        state, model_event.usage
                    ):
                        yield event
        except (AgentEngineError, CancellationError):
            raise
        except Exception:
            raise ModelStreamError("model stream failed") from None
        if not completed:
            raise ModelStreamError("model stream ended before completion")

    async def _record_model_usage(
        self, state: _RunState, usage: Usage
    ) -> AsyncIterator[AgentEvent]:
        state.total_usage = add_usage(state.total_usage, usage)
        if state.task is not None:
            await self._journal.consume_task_usage(state.task.id, usage)
            thresholds = await self._journal.mark_task_budget_warnings(
                state.task.id
            )
            for threshold in thresholds:
                warning = AgentEvent(
                    EventKind.TASK_BUDGET_WARNING,
                    {
                        "task_id": state.task.id,
                        "threshold": threshold,
                        "reason": f"token budget reached {threshold}%",
                    },
                )
                await self._journal.append_event(state.thread_id, warning)
                yield warning
        if state.total_usage.total_tokens > self._limits.max_total_tokens:
            raise EngineLimitError("token budget exceeded")
```

Use this complete target for `_engine_turn.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator

from ._engine_run import _RunState, _TurnState
from .errors import EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .models import Message
from .task import TaskRecord, TaskStatus
from .task_supervisor import SupervisionKind


class AgentEngineTurnMixin:
    """Internal orchestration for one model-and-action turn."""

    async def _run_turn(
        self, state: _RunState, number: int, user_input: str
    ) -> AsyncIterator[AgentEvent]:
        state.token.raise_if_cancelled()
        paused = await self._before_model_turn(state)
        if paused is not None:
            yield paused
            state.stop_requested = True
            return
        turn, started = await self._start_turn(state, number)
        yield started
        bundle = await self._build_turn_context(state, turn, user_input)
        built = AgentEvent(
            EventKind.CONTEXT_BUILT,
            {"turn": number, **bundle.measurements},
        )
        await self._journal.append_event(state.thread_id, built)
        yield built
        model_started = AgentEvent(
            EventKind.MODEL_STARTED, {"turn": number}
        )
        await self._journal.append_event(state.thread_id, model_started)
        yield model_started
        async for event in self._stream_model_events(state, turn, bundle):
            yield event
        assistant, added = await self._persist_assistant_message(
            state.thread_id, turn.text_parts, turn.calls
        )
        state.messages += (assistant,)
        yield added
        if not turn.calls:
            async for event in self._finish_without_calls(state, turn):
                yield event
            return
        await self._reserve_tool_calls(state, turn)
        async for event in self._dispatch_tool_calls(state, turn):
            yield event

    async def _before_model_turn(
        self, state: _RunState
    ) -> AgentEvent | None:
        supervisor = state.supervisor
        task = state.task
        if supervisor is None or task is None:
            return None
        decision = supervisor.before_model_turn()
        if decision.kind is SupervisionKind.PAUSE:
            reason = decision.reason or "task paused"
            await self._pause_task(
                state.thread_id, task, supervisor, reason
            )
            paused = AgentEvent(
                EventKind.TASK_PAUSED,
                {"task_id": task.id, "status": "paused", "reason": reason},
            )
            await self._journal.append_event(state.thread_id, paused)
            return paused
        await self._journal.record_task_active_seconds(
            task.id, supervisor.checkpoint_active_seconds()
        )
        await self._journal.consume_task_controls(task.id)
        return None

    async def _start_turn(
        self, state: _RunState, number: int
    ) -> tuple[_TurnState, AgentEvent]:
        reserved = await self._journal.reserve_task_budget(
            state.thread_id, model_turns=1
        )
        if reserved is None:
            raise EngineLimitError("model turn budget exceeded")
        state.budget = reserved
        tools, tool_names = self._advertised_tools()
        started = AgentEvent(
            kind=EventKind.TURN_STARTED,
            payload={"turn": number},
        )
        await self._journal.append_event(state.thread_id, started)
        return _TurnState(number, tools, tool_names), started

    async def _finish_without_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        if state.task is None:
            finished = self._completed_event(
                state.thread_id, state.budget, state.total_usage
            )
            await self._journal.append_event(state.thread_id, finished)
            yield finished
            state.stop_requested = True
            return
        async for event in self._finish_task_without_calls(state, turn):
            yield event

    async def _finish_task_without_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        task = state.task
        if task is None:
            return
        if state.supervisor is not None:
            await self._journal.record_task_active_seconds(
                task.id, state.supervisor.checkpoint_active_seconds()
            )
        automatic = await self._run_suggested_verification(
            state.thread_id,
            task,
            state.token,
            state.supervisor,
            state.budget,
            turn.tool_names,
        )
        if automatic is not None:
            state.budget, events = automatic
            for event in events:
                yield event
            if self._should_stop_after_action(events):
                state.stop_requested = True
                return
            next_task = await self._resolve_task_completion(
                task, state.thread_id
            )
            if next_task.status is TaskStatus.RUNNING:
                return
        else:
            next_task = await self._resolve_task_completion(
                task, state.thread_id
            )
        async for event in self._persist_completion_events(state, next_task):
            yield event
        state.stop_requested = True

    async def _persist_completion_events(
        self, state: _RunState, task: TaskRecord
    ) -> AsyncIterator[AgentEvent]:
        events = self._task_completion_events(
            state.thread_id, task, state.budget, state.total_usage
        )
        for event in events:
            await self._journal.append_event(state.thread_id, event)
            yield event

    async def _reserve_tool_calls(
        self, state: _RunState, turn: _TurnState
    ) -> None:
        calls = turn.calls
        if state.budget.model_turns >= state.budget.limits.max_agent_rounds:
            raise EngineLimitError("model turn budget exceeded")
        if len(calls) > state.budget.limits.max_tool_calls_per_round:
            raise EngineLimitError("tool call per-round budget exceeded")
        reserved = await self._journal.reserve_task_budget(
            state.thread_id, tool_calls=len(calls)
        )
        if reserved is None:
            raise EngineLimitError("tool call budget exceeded")
        state.budget = reserved
        duplicate = len({call.id for call in calls}) != len(calls)
        reused = any(call.id in state.used_call_ids for call in calls)
        if duplicate or reused:
            raise ModelStreamError("model reused a tool call id")

    async def _dispatch_tool_calls(
        self, state: _RunState, turn: _TurnState
    ) -> AsyncIterator[AgentEvent]:
        for call in turn.calls:
            state.used_call_ids.add(call.id)
            async for event in self._dispatch(
                state.thread_id,
                call,
                state.token,
                is_available=call.name in turn.tool_names,
                task=state.task,
                supervisor=state.supervisor,
            ):
                if event.kind is EventKind.MESSAGE_ADDED:
                    result_message = Message.from_dict(
                        event.payload["message"]  # type: ignore[arg-type]
                    )
                    state.messages += (result_message,)
                yield event
                if event.kind in {
                    EventKind.TASK_PAUSED,
                    EventKind.TASK_DECISION_REQUIRED,
                }:
                    state.stop_requested = True
                    return
```

Use this complete target for `engine.py`:

```python
from __future__ import annotations

from typing import AsyncIterator, Mapping, Optional

from ._engine_run import AgentEngineRunMixin, _validate_run_arguments
from ._engine_turn import AgentEngineTurnMixin
from ._json import JSONValue, freeze_mapping
from ._session_io import SessionJournal
from .cancellation import CancellationError, CancellationToken
from .engine_actions import AgentEngineActionMixin
from .engine_completion import AgentEngineCompletionMixin
from .errors import AgentEngineError, EngineLimitError
from .events import AgentEvent, EventKind
from .limits import EngineLimits
from .protocols import (
    ActionDispatcher,
    ContextBuilder,
    ModelClient,
    SessionRepository,
)
from .task import TaskRecord
from .task_verification import TaskVerificationService


class AgentEngine(
    AgentEngineCompletionMixin,
    AgentEngineActionMixin,
    AgentEngineRunMixin,
    AgentEngineTurnMixin,
):
    def __init__(
        self,
        model: ModelClient,
        context: ContextBuilder,
        actions: ActionDispatcher,
        sessions: SessionRepository,
        *,
        limits: Optional[EngineLimits] = None,
        model_name: str = "configured-model",
        verification: TaskVerificationService | None = None,
        context_mode_snapshot: Mapping[str, JSONValue] | None = None,
        context_permission_snapshot: Mapping[str, JSONValue] | None = None,
    ) -> None:
        self._model = model
        self._context = context
        self._actions = actions
        self._journal = SessionJournal(sessions)
        self._limits = limits or EngineLimits()
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be non-blank text")
        self._model_name = model_name
        self._verification = verification
        self._context_mode_snapshot = freeze_mapping(
            {} if context_mode_snapshot is None else context_mode_snapshot,
            "context_mode_snapshot",
        )
        self._context_permission_snapshot = freeze_mapping(
            (
                {}
                if context_permission_snapshot is None
                else context_permission_snapshot
            ),
            "context_permission_snapshot",
        )

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        _validate_run_arguments(user_input, thread_id)
        state, started = await self._start_run(
            thread_id, cancellation, task
        )
        yield started
        try:
            added, user_message = await self._prepare_request(
                state, user_input
            )
            yield added
            state.messages = state.prior_messages + (user_message,)
            turn_limit = state.budget.limits.max_agent_rounds
            for turn in range(1, turn_limit + 1):
                async for event in self._run_turn(
                    state, turn, user_input
                ):
                    yield event
                if state.stop_requested:
                    return
            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(state.thread_id, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={
                    "code": exc.code,
                    "error_type": type(exc).__name__,
                },
            )
            await self._journal.append_event(state.thread_id, failed)
            yield failed
            raise
```

- [ ] **Step 6: Verify exact engine behavior after the mechanical split**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_engine_run `
  src.code_agent.core.tests.test_engine_limits `
  src.code_agent.core.tests.test_engine_tools -v
& $python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/core
git diff --check
```

Run the structural gate before committing the split:

```powershell
$python = ".venv\Scripts\python.exe"
$code = @'
import ast
from pathlib import Path

paths = (
    Path("src/code_agent/core/engine.py"),
    Path("src/code_agent/core/_engine_run.py"),
    Path("src/code_agent/core/_engine_turn.py"),
)
violations = []
for path in paths:
    source = path.read_text(encoding="utf-8")
    if len(source.splitlines()) > 300:
        violations.append(f"{path}: file exceeds 300 lines")
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > 50:
                violations.append(f"{path}:{node.lineno} {node.name}: {size}")
if violations:
    raise SystemExit("\n".join(violations))
'@
& $python -c $code
```

Expected: all tests pass with no expected-event changes and the structural gate
prints nothing.

- [ ] **Step 7: Commit the run-loop split**

```powershell
git add src/code_agent/core/_engine_run.py `
  src/code_agent/core/_engine_turn.py `
  src/code_agent/core/engine.py
git commit -m "拆分核心运行循环：按持久化阶段组织回合"
```

---

### Task 0C: Split the existing action dispatch function at side-effect boundaries

**Files:**

- Modify: `src/code_agent/core/engine_actions.py`
- Test: `src/code_agent/core/tests/test_engine_tools.py`
- Test: `src/code_agent/core/tests/test_engine_run.py`

- [ ] **Step 1: Freeze action event and persistence behavior**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_engine_tools `
  src.code_agent.core.tests.test_engine_run -v
```

Expected: existing action tests pass before the move.

- [ ] **Step 2: Extract existing behavior into two bounded helpers**

The minimal behavior-preserving split moves only the existing state-recording
block (`engine_actions.py:63-78`) into:

```python
async def _record_action_state(
    self,
    thread_id: str,
    call: ToolCall,
    request: ActionRequest,
    result: ActionResult,
    task: TaskRecord | None,
    supervisor: TaskSupervisor | None,
) -> AgentEvent | None:
    if call.name not in {
        "read_file",
        "list_files",
        "search_text",
        "write_file",
        "replace_text",
        "run_command",
        "run_verification",
    }:
        return None
    state = await self._journal.reduce_task_state(thread_id, request, result)
    verification = getattr(self, "_verification", None)
    if task is not None and verification is not None:
        state = await verification.record_action(task, request, result, state)
        await self._journal.save_task_state(thread_id, state)
    if task is None or supervisor is None or call.name != "run_verification":
        return None
    return await self._record_validation_state(
        thread_id, request, result, task, supervisor, state
    )

async def _record_validation_state(
    self,
    thread_id: str,
    request: ActionRequest,
    result: ActionResult,
    task: TaskRecord,
    supervisor: TaskSupervisor,
    state: TaskState,
) -> AgentEvent | None:
    fingerprint = _validation_fingerprint(request, result)
    changed_files = len(state.files_changed)
    decision = supervisor.observe_validation(fingerprint, changed_files)
    await self._journal.observe_task_validation(
        task.id, fingerprint, changed_files
    )
    await self._journal.create_checkpoint(
        thread_id,
        "validation-complete",
        {"task_id": task.id, "failed": fingerprint is not None},
    )
    if decision.kind is not SupervisionKind.PAUSE:
        return None
    reason = decision.reason or "validation paused"
    await self._pause_task(thread_id, task, supervisor, reason)
    paused = AgentEvent(
        EventKind.TASK_PAUSED,
        {"task_id": task.id, "status": "paused", "reason": reason},
    )
    await self._journal.append_event(thread_id, paused)
    return paused
```

Add `ActionResult` to the existing `.models` import and import `TaskState` from
`.task_state`. Each helper remains below 50 lines.

Replace the old block inside `_dispatch` with:

```python
paused = await self._record_action_state(
    thread_id, call, request, result, task, supervisor
)
if paused is not None:
    yield paused
```

Do not return after this validation pause: existing behavior still persists and
yields `ACTION_COMPLETED` and the tool `MESSAGE_ADDED`. Keep the existing
preflight, dispatcher invocation, decision-required branch and feedback code in
their original order. Task 2 will extract dispatcher invocation only when
adding the lineage keyword.

Rewrite `_dispatch` as orchestration only:

```text
persist/yield ACTION_REQUESTED
→ unavailable failure OR preflight pause
→ persist/yield ACTION_STARTED
→ invoke dispatcher
→ decision-required early return
→ record task/verification state and optional pause event
→ persist/yield ACTION_COMPLETED and MESSAGE_ADDED
```

Both `_dispatch` and every helper must be at most 50 lines. Task 2 will add
execution-context construction after the caller has durably persisted
`ACTION_STARTED`.

- [ ] **Step 3: Verify the mechanical split and all structural constraints**

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_engine_tools `
  src.code_agent.core.tests.test_engine_run -v
& $python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/core
git diff --check
```

Run the complete Core structural gate before committing:

```powershell
$python = ".venv\Scripts\python.exe"
$code = @'
import ast
from pathlib import Path

violations = []
for path in Path("src/code_agent/core").rglob("*.py"):
    source = path.read_text(encoding="utf-8")
    if len(source.splitlines()) > 300:
        violations.append(f"{path}: file exceeds 300 lines")
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > 50:
                violations.append(f"{path}:{node.lineno} {node.name}: {size}")
if violations:
    raise SystemExit("\n".join(violations))
'@
& $python -c $code
```

Expected: no file over 300 lines and no function over 50 lines before lineage
code is added.

- [ ] **Step 4: Commit the action split**

```powershell
git add src/code_agent/core/engine_actions.py
git commit -m "拆分核心动作分发：隔离副作用边界"
```

---

### Task 1: Add immutable and bounded action execution values

**Files:**

- Create: `src/code_agent/core/action_execution.py`
- Create: `src/code_agent/core/tests/test_action_execution.py`

- [ ] **Step 1: Write the failing value-object tests**

Create `test_action_execution.py`:

```python
from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.action_execution import (  # noqa: E402
    ActionExecutionContext,
    ActionLineage,
)


class ActionExecutionValueTests(unittest.TestCase):
    def test_context_keeps_owner_and_origin_distinct(self) -> None:
        value = ActionExecutionContext(
            owner_thread_id="parent",
            origin_thread_id="child",
            request_id="request",
            task_id="task",
            parent_request_id="delegate",
        )

        self.assertEqual(value.owner_thread_id, "parent")
        self.assertEqual(value.origin_thread_id, "child")
        self.assertEqual(value.request_id, "request")
        self.assertEqual(value.task_id, "task")
        self.assertEqual(value.parent_request_id, "delegate")

    def test_required_identifiers_are_bounded_nonblank_text(self) -> None:
        cases = (
            lambda value: ActionLineage(value),
            lambda value: ActionExecutionContext(value, "origin", "request"),
            lambda value: ActionExecutionContext("owner", value, "request"),
            lambda value: ActionExecutionContext("owner", "origin", value),
        )
        for constructor in cases:
            for invalid in (" ", "x" * 257, 3):
                with self.subTest(constructor=constructor, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        constructor(invalid)  # type: ignore[arg-type]

    def test_optional_identifiers_are_none_or_bounded_nonblank_text(self) -> None:
        for invalid in (" ", "x" * 257, 3):
            with self.subTest(field="lineage.task", invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    ActionLineage("owner", task_id=invalid)  # type: ignore[arg-type]
            with self.subTest(field="lineage.parent", invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    ActionLineage(
                        "owner", parent_request_id=invalid  # type: ignore[arg-type]
                    )
            with self.subTest(field="context.task", invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    ActionExecutionContext(
                        "owner", "origin", "request", task_id=invalid  # type: ignore[arg-type]
                    )
            with self.subTest(field="context.parent", invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    ActionExecutionContext(
                        "owner",
                        "origin",
                        "request",
                        parent_request_id=invalid,  # type: ignore[arg-type]
                    )

    def test_values_are_frozen(self) -> None:
        lineage = ActionLineage("owner")
        context = ActionExecutionContext("owner", "origin", "request")

        with self.assertRaises(FrozenInstanceError):
            lineage.owner_thread_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            context.request_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_action_execution -v
```

Expected: FAIL because `code_agent.core.action_execution` does not exist.

- [ ] **Step 3: Implement the minimal immutable models**

Create `action_execution.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from ._json import validate_identifier


def _validate_optional_identifier(value: object, label: str) -> None:
    if value is not None:
        validate_identifier(value, label)


@dataclass(frozen=True)
class ActionLineage:
    owner_thread_id: str
    task_id: str | None = None
    parent_request_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.owner_thread_id, "owner_thread_id")
        _validate_optional_identifier(self.task_id, "task_id")
        _validate_optional_identifier(self.parent_request_id, "parent_request_id")


@dataclass(frozen=True)
class ActionExecutionContext:
    owner_thread_id: str
    origin_thread_id: str
    request_id: str
    task_id: str | None = None
    parent_request_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.owner_thread_id, "owner_thread_id")
        validate_identifier(self.origin_thread_id, "origin_thread_id")
        validate_identifier(self.request_id, "request_id")
        _validate_optional_identifier(self.task_id, "task_id")
        _validate_optional_identifier(self.parent_request_id, "parent_request_id")
```

- [ ] **Step 4: Run RED-to-GREEN verification**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_action_execution -v
& $python -m compileall -q src/code_agent/core/action_execution.py
git diff --check
```

Expected: 4 focused tests pass.

- [ ] **Step 5: Commit the value objects**

```powershell
git add src/code_agent/core/action_execution.py `
  src/code_agent/core/tests/test_action_execution.py
git commit -m "新增动作执行上下文：冻结父子归因标识"
```

---

### Task 2: Propagate execution context through the Core dispatcher boundary

**Files:**

- Modify: `src/code_agent/core/protocols.py`
- Modify: `src/code_agent/core/engine.py`
- Modify: `src/code_agent/core/engine_actions.py`
- Modify: `src/code_agent/core/tests/_engine_support.py`
- Create: `src/code_agent/core/tests/test_engine_action_lineage.py`
- Modify: `src/code_agent/core/tests/test_engine_tools.py`
- Modify: `src/code_agent/core/tests/test_protocols.py`

- [ ] **Step 1: Upgrade Core recording fakes before writing propagation assertions**

In `_engine_support.py`, import the typed context and task authorization:

```python
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.task import TaskAuthorization
```

Initialize two new recording lists in `FakeActionDispatcher.__init__`:

```python
self.authorizations: list[TaskAuthorization | None] = []
self.contexts: list[ActionExecutionContext | None] = []
```

Replace its dispatch signature/body prefix with:

```python
async def dispatch(
    self,
    request: ActionRequest,
    cancellation: CancellationToken,
    task_authorization: TaskAuthorization | None = None,
    *,
    execution_context: ActionExecutionContext | None = None,
) -> ActionResult:
    self.requests.append(request)
    self.tokens.append(cancellation)
    self.authorizations.append(task_authorization)
    self.contexts.append(execution_context)
    outcome = self.outcomes.pop(0)
```

Keep the existing outcome/exception behavior after that prefix.

In `test_protocols.py`, give its local fake the same keyword-only parameter and a `context` field:

```python
from code_agent.core.action_execution import ActionExecutionContext  # noqa: E402
from code_agent.core.task import TaskAuthorization  # noqa: E402


class FakeActionDispatcher:
    def __init__(self) -> None:
        self.context: ActionExecutionContext | None = None

    def tools(self) -> Sequence[ToolDefinition]:
        return ()

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        self.context = execution_context
        return ActionResult(request_id=request.id, name=request.name, output=None)
```

Do not yet change production protocol/engine code; the new assertions in the next step must still fail.

- [ ] **Step 2: Write ad-hoc root and unavailable assertions in the existing tool tests**

Add imports to `test_engine_tools.py`:

```python
from code_agent.core.action_execution import ActionExecutionContext  # noqa: E402
```

In the existing `test_tool_result_is_paired_and_returned_to_next_model_turn`,
add:

```python
self.assertEqual(
    actions.contexts,
    [ActionExecutionContext("thread-1", "thread-1", "call-1")],
)
```

In the existing unadvertised-tool test add:

```python
self.assertEqual(actions.contexts, [])
```

- [ ] **Step 3: Write task and child propagation tests in a focused file**

Create `test_engine_action_lineage.py` so `test_engine_tools.py` stays well below
300 lines:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.action_execution import (  # noqa: E402
    ActionExecutionContext,
    ActionLineage,
)
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.errors import SessionPersistenceError  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionResult,
    ModelEvent,
    ModelEventKind,
    ToolCall,
)
from code_agent.core.task import (  # noqa: E402
    TaskAuthorization,
    TaskContract,
    TaskRecord,
    TaskStatus,
)
from code_agent.core.task_supervisor import (  # noqa: E402
    SupervisionDecision,
    SupervisionKind,
)
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def completed() -> ModelEvent:
    return ModelEvent(ModelEventKind.COMPLETED)


class FailingActionStartedSessions(MemorySessionRepository):
    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        if event.kind is EventKind.ACTION_STARTED:
            raise RuntimeError("injected persistence failure")
        await super().append_event(thread_id, event)


class PausingSupervisor:
    def before_external_action(self) -> SupervisionDecision:
        return SupervisionDecision(SupervisionKind.PAUSE, "injected pause")


class ContextSpyEngine(AgentEngine):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.context_builds = 0

    def _action_execution_context(
        self,
        thread_id: str,
        request_id: str,
        task: TaskRecord | None,
    ) -> ActionExecutionContext:
        self.context_builds += 1
        return super()._action_execution_context(thread_id, request_id, task)

    async def _pause_task(
        self,
        thread_id: str,
        task: TaskRecord,
        supervisor: object,
        reason: str,
    ) -> None:
        return None


class AgentEngineActionLineageTests(unittest.IsolatedAsyncioTestCase):
    def test_constructor_rejects_untyped_lineage(self) -> None:
        with self.assertRaisesRegex(
            TypeError, "action_lineage must be an ActionLineage or None"
        ):
            AgentEngine(
                FakeModelClient(()),
                FakeContextBuilder(),
                FakeActionDispatcher(),
                MemorySessionRepository(),
                action_lineage="parent",  # type: ignore[arg-type]
            )

    async def test_root_task_context_uses_current_task_id(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})
        result = ActionResult("call-1", "read_file", {"text": "contents"})
        actions = FakeActionDispatcher((result,))
        engine = AgentEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        task = TaskRecord(
            "task-1",
            thread_id,
            TaskContract(
                "inspect", TaskAuthorization.local_workspace("C:/repo")
            ),
            TaskStatus.RUNNING,
        )

        _ = [
            event
            async for event in engine._dispatch(
                thread_id,
                call,
                CancellationToken(),
                is_available=True,
                task=task,
            )
        ]

        self.assertEqual(
            actions.contexts,
            [
                ActionExecutionContext(
                    thread_id, thread_id, "call-1", task_id="task-1"
                )
            ],
        )
        self.assertEqual(actions.authorizations, [task.contract.authorization])

    async def test_child_context_preserves_parent_lineage(self) -> None:
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})
        model = FakeModelClient(
            (
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), completed()),
                (completed(),),
            )
        )
        actions = FakeActionDispatcher(
            (ActionResult("call-1", "read_file", {"text": "contents"}),)
        )
        sessions = MemorySessionRepository()
        child_thread = await sessions.create_thread()
        engine = AgentEngine(
            model,
            FakeContextBuilder(),
            actions,
            sessions,
            action_lineage=ActionLineage(
                "parent-thread", "parent-task", "delegate-request"
            ),
        )

        _ = [
            event
            async for event in engine.run("inspect", thread_id=child_thread)
        ]

        self.assertEqual(
            actions.contexts,
            [
                ActionExecutionContext(
                    "parent-thread",
                    child_thread,
                    "call-1",
                    "parent-task",
                    "delegate-request",
                )
            ],
        )

    async def test_supervisor_pause_never_builds_or_dispatches_context(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        actions = FakeActionDispatcher()
        engine = ContextSpyEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        task = TaskRecord(
            "task-1",
            thread_id,
            TaskContract(
                "write", TaskAuthorization.local_workspace("C:/repo")
            ),
            TaskStatus.RUNNING,
        )
        call = ToolCall("call-1", "write_file", {"path": "a.txt"})

        events = [
            event
            async for event in engine._dispatch(
                thread_id,
                call,
                CancellationToken(),
                is_available=True,
                task=task,
                supervisor=PausingSupervisor(),  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(events[-1].kind, EventKind.TASK_PAUSED)
        self.assertNotIn(EventKind.ACTION_STARTED, [e.kind for e in events])
        self.assertEqual(engine.context_builds, 0)
        self.assertEqual(actions.requests, [])
        self.assertEqual(actions.contexts, [])

    async def test_started_persistence_precedes_context_construction(self) -> None:
        sessions = FailingActionStartedSessions()
        thread_id = await sessions.create_thread()
        actions = FakeActionDispatcher(
            (ActionResult("call-1", "read_file", {"text": "unused"}),)
        )
        engine = ContextSpyEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})

        with self.assertRaises(SessionPersistenceError):
            _ = [
                event
                async for event in engine._dispatch(
                    thread_id,
                    call,
                    CancellationToken(),
                    is_available=True,
                )
            ]

        self.assertEqual(engine.context_builds, 0)
        self.assertEqual(actions.requests, [])
        self.assertEqual(actions.contexts, [])


if __name__ == "__main__":
    unittest.main()
```

This file remains below 300 lines; every test/helper is below 50 lines.

- [ ] **Step 4: Exercise the protocol keyword explicitly**

In `test_protocols.py`, import `Parameter`/`signature`, inspect the declared
Protocol method, then pass and assert the exact keyword on the fake:

```python
from inspect import Parameter, signature


    def test_action_dispatcher_declares_keyword_only_context(self) -> None:
        parameter = signature(ActionDispatcher.dispatch).parameters[
            "execution_context"
        ]

        self.assertIs(parameter.kind, Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

    async def test_action_dispatcher_fake_dispatches_request(self) -> None:
        dispatcher: ActionDispatcher = FakeActionDispatcher()
        request = ActionRequest(
            id="action-1", name="read_file", arguments={}
        )
        context = ActionExecutionContext("owner", "origin", "action-1")

        result = await dispatcher.dispatch(
            request, CancellationToken(), execution_context=context
        )

        self.assertEqual(dispatcher.tools(), ())
        self.assertEqual(dispatcher.context, context)
        self.assertEqual(
            result,
            ActionResult(
                request_id="action-1",
                name="read_file",
                output=None,
            ),
        )
```

- [ ] **Step 5: Run the propagation tests to verify RED**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_engine_action_lineage `
  src.code_agent.core.tests.test_engine_tools `
  src.code_agent.core.tests.test_protocols -v
```

Expected failures:

- `AgentEngine.__init__` rejects `action_lineage`;
- production dispatch does not pass `execution_context`;
- production protocol does not yet declare the keyword.

- [ ] **Step 6: Extend the protocol with a keyword-only context**

In `protocols.py`, import the context:

```python
from .action_execution import ActionExecutionContext
```

Replace only the dispatcher method signature:

```python
async def dispatch(
    self,
    request: ActionRequest,
    cancellation: CancellationToken,
    task_authorization: TaskAuthorization | None = None,
    *,
    execution_context: ActionExecutionContext | None = None,
) -> ActionResult: ...
```

- [ ] **Step 7: Store a validated optional lineage on the engine**

In the already-split `engine.py`, import `ActionLineage` and add the constructor
keyword:

```python
from .action_execution import ActionLineage

def __init__(
    self,
    model: ModelClient,
    context: ContextBuilder,
    actions: ActionDispatcher,
    sessions: SessionRepository,
    *,
    limits: Optional[EngineLimits] = None,
    model_name: str = "configured-model",
    verification: TaskVerificationService | None = None,
    context_mode_snapshot: Mapping[str, JSONValue] | None = None,
    context_permission_snapshot: Mapping[str, JSONValue] | None = None,
    action_lineage: ActionLineage | None = None,
) -> None:
```

After assigning `_limits`, validate and store it:

```python
if action_lineage is not None and not isinstance(action_lineage, ActionLineage):
    raise TypeError("action_lineage must be an ActionLineage or None")
self._action_lineage = action_lineage
```

The Task 0B split leaves `engine.py` well below its limit; keep the constructor
readable and do not change run-loop behavior in this step.

- [ ] **Step 8: Build the context only after durable `ACTION_STARTED`**

In `engine_actions.py`, import the two value objects:

```python
from .action_execution import ActionExecutionContext, ActionLineage
```

Add this small helper to `AgentEngineActionMixin`:

```python
def _action_execution_context(
    self,
    thread_id: str,
    request_id: str,
    task: TaskRecord | None,
) -> ActionExecutionContext:
    lineage: ActionLineage | None = self._action_lineage
    return ActionExecutionContext(
        owner_thread_id=lineage.owner_thread_id if lineage else thread_id,
        origin_thread_id=thread_id,
        request_id=request_id,
        task_id=lineage.task_id if lineage else task.id if task else None,
        parent_request_id=lineage.parent_request_id if lineage else None,
    )
```

Add a bounded invocation helper:

```python
async def _invoke_action(
    self,
    request: ActionRequest,
    call: ToolCall,
    token: CancellationToken,
    task: TaskRecord | None,
    execution_context: ActionExecutionContext,
) -> ActionResult:
    try:
        if task is not None:
            result = await self._actions.dispatch(
                request,
                token,
                task.contract.authorization,
                execution_context=execution_context,
            )
        else:
            result = await self._actions.dispatch(
                request,
                token,
                execution_context=execution_context,
            )
        if result.request_id != call.id or result.name != call.name:
            return tool_failure(call, "invalid tool result")
        return result
    except CancellationError:
        raise
    except Exception as exc:
        return tool_failure(
            call, "tool execution failed", type(exc).__name__
        )
```

Inside `_dispatch`, leave unavailable and supervisor-pause branches unchanged.
Immediately after `ACTION_STARTED` has been appended and yielded, use:

```python
execution_context = self._action_execution_context(thread_id, call.id, task)
result = await self._invoke_action(
    request, call, token, task, execution_context
)
```

Thus a failed `ACTION_STARTED` append cannot reach either helper. Do not attach
context to public events, and do not retry a dispatcher that rejects the
keyword. `_dispatch`, `_invoke_action` and `_action_execution_context` must each
remain below 50 lines.

- [ ] **Step 9: Run focused and full Core GREEN**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest src.code_agent.core.tests.test_action_execution `
  src.code_agent.core.tests.test_engine_action_lineage `
  src.code_agent.core.tests.test_engine_tools `
  src.code_agent.core.tests.test_protocols -v
& $python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/core
git diff --check
```

Expected: all focused tests and the expanded Core suite pass.

- [ ] **Step 10: Commit explicit propagation**

```powershell
git add src/code_agent/core/protocols.py `
  src/code_agent/core/engine.py `
  src/code_agent/core/engine_actions.py `
  src/code_agent/core/tests/_engine_support.py `
  src/code_agent/core/tests/test_engine_action_lineage.py `
  src/code_agent/core/tests/test_engine_tools.py `
  src/code_agent/core/tests/test_protocols.py
git commit -m "传递动作归因：显式区分父子线程执行上下文"
```

---

### Task 3: Update the Core contract and run the Feature gate

**Files:**

- Modify: `src/code_agent/core/AGENTS.md`

- [ ] **Step 1: Update only the Units section**

Keep the already approved goal/boundary text. Add:

```markdown
- `ActionLineage`: 冻结父动作传给 child engine 的 owner、task 与 parent request | 无副作用 | 不携带 child 自身 origin/request
- `ActionExecutionContext`: 冻结单次实际 dispatcher 调用的 owner/origin/task/request/parent request | 无副作用 | 所有存在的标识均为有界非空文本
```

Replace the current `ActionDispatcher` Unit with:

```markdown
- `ActionDispatcher`: 暴露工具并接收可取消动作、可选任务授权和 keyword-only execution context | 具体副作用由实现负责 | unavailable/dispatch 前暂停不产生执行上下文
```

Replace the two existing `AgentEngine.run` lines with:

```markdown
- `AgentEngine.run(..., task=...)`: 在同一 thread 内执行显式任务并持久化任务事件 | 调用抽象模型、动作与会话协议 | root 动作使用 active thread/task 归因；安全边界消费 steering，自动验证通过后由持久完成门收尾
- `AgentEngine.run(user_input, thread_id, cancellation)`: 持久化并流式发布回合、模型、工具和终态事件 | 调用抽象模型、动作与会话协议 | ad-hoc root 的 owner/origin 均为 active thread，child engine 继承构造器 lineage；未声明工具、重复 ID、无完成事件和预算越界均失败闭合
```

Do not claim that Core persists mutations or coverage.

- [ ] **Step 2: Run the full Core verification gate**

Run:

```powershell
$python = ".venv\Scripts\python.exe"
& $python -m unittest discover -s src/code_agent/core/tests -p "test_*.py" -v
& $python -m compileall -q src/code_agent/core
git diff --check
git status --short
```

Run the structural AST gate:

```powershell
$python = ".venv\Scripts\python.exe"
$code = @'
import ast
from pathlib import Path

violations = []
for path in Path("src/code_agent/core").rglob("*.py"):
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    if len(lines) > 300:
        violations.append(f"{path}: file has {len(lines)} lines")
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > 50:
                violations.append(
                    f"{path}:{node.lineno} {node.name} has {size} lines"
                )
if violations:
    raise SystemExit("\n".join(violations))
'@
& $python -c $code
```

Expected: no violations, no unexpected files outside the Core Feature and plan
document, and a passing expanded Core suite.

- [ ] **Step 3: Commit the Feature contract**

```powershell
git add src/code_agent/core/AGENTS.md
git commit -m "更新核心契约：记录动作执行归因"
```

- [ ] **Step 4: Run independent review gates**

Dispatch two fresh reviewers:

1. Specification review: compare every invariant in this plan and upstream
   roadmap Task 5 with the diff and tests.
2. Quality review: inspect identifier bounds, dataclass immutability, context
   construction order, unavailable/pause paths, task precedence, exception
   behavior, fake fidelity, public model exports and Python structural limits.

Any Critical or Important finding returns to the owning task for a regression
test, minimal fix, Core gate rerun and fresh review. Both reviews must report
PASS before starting the Interfaces Feature.
