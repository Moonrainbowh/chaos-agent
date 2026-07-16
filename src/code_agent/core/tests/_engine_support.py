from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence

from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.events import AgentEvent
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
    ContextBundle,
    Message,
    ModelEvent,
    ToolDefinition,
)
from code_agent.core.task_state import TaskState
from code_agent.core.task_state import reduce_task_state
from code_agent.core.limits import EngineLimits, TaskBudget


class FakeModelClient:
    def __init__(self, streams: Sequence[Sequence[ModelEvent] | BaseException]):
        self.streams = list(streams)
        self.calls: list[tuple[str, tuple[Message, ...], tuple[ToolDefinition, ...]]] = []

    def stream(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]:
        self.calls.append((system_prompt, tuple(messages), tuple(tools)))
        stream = self.streams.pop(0)

        async def generate() -> AsyncIterator[ModelEvent]:
            if isinstance(stream, BaseException):
                raise stream
            for event in stream:
                yield event

        return generate()


class FakeContextBuilder:
    def __init__(self, measurements: Mapping[str, int] | None = None) -> None:
        self.calls: list[tuple[tuple[Message, ...], str, tuple[ToolDefinition, ...], TaskState]] = []
        self.requests: list[ContextRequest] = []
        self.measurements = dict(measurements or {})

    async def build(self, request: ContextRequest) -> ContextBundle:
        self.requests.append(request)
        history = request.messages
        self.calls.append(
            (history, request.user_input, request.tools, request.task_state)
        )
        if request.user_input:
            history += (Message(role="user", content=request.user_input),)
        return ContextBundle(
            system_prompt="system",
            messages=history,
            measurements=self.measurements,
        )


class FakeActionDispatcher:
    def __init__(
        self,
        outcomes: Sequence[ActionResult | BaseException] = (),
    ) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ActionRequest] = []
        self.tokens: list[CancellationToken] = []
        self._tools = (
            ToolDefinition(
                name="read_file",
                description="Read a file",
                parameters={"type": "object"},
            ),
        )

    def tools(self) -> Sequence[ToolDefinition]:
        return self._tools

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken, *args: object
    ) -> ActionResult:
        self.requests.append(request)
        self.tokens.append(cancellation)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MemorySessionRepository:
    def __init__(self) -> None:
        self.messages: dict[str, list[Message]] = {}
        self.events: dict[str, list[AgentEvent]] = {}
        self.created = 0
        self.task_states: dict[str, TaskState] = {}
        self.task_budgets: dict[str, TaskBudget] = {}

    async def create_thread(self) -> str:
        self.created += 1
        thread_id = f"thread-{self.created}"
        self.messages[thread_id] = []
        self.events[thread_id] = []
        self.task_states[thread_id] = TaskState.empty()
        return thread_id

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget:
        return self.task_budgets.setdefault(thread_id, TaskBudget(model_name, limits))

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None:
        current = self.task_budgets[thread_id]
        if current.model_turns + model_turns > current.limits.max_agent_rounds or current.tool_calls + tool_calls > current.limits.max_tool_calls:
            return None
        next_budget = TaskBudget(current.model_name, current.limits, current.model_turns + model_turns, current.tool_calls + tool_calls)
        self.task_budgets[thread_id] = next_budget
        return next_budget

    async def load_messages(self, thread_id: str) -> Sequence[Message]:
        return tuple(self.messages[thread_id])

    async def append_message(self, thread_id: str, message: Message) -> None:
        self.messages[thread_id].append(message)

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        self.events[thread_id].append(event)

    async def load_task_state(self, thread_id: str) -> TaskState:
        return self.task_states[thread_id]

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        self.task_states[thread_id] = state

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState:
        state = reduce_task_state(self.task_states[thread_id], request, result)
        self.task_states[thread_id] = state
        return state

    async def record_task_active_seconds(self, task_id: str, active_seconds: int) -> TaskBudget:
        raise KeyError(task_id)

    async def mark_task_budget_warnings(self, task_id: str) -> tuple[int, ...]:
        return ()

    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]:
        return ()
