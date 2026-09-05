"""Guard every main and auxiliary request against the same durable budget."""
import uuid

from code_agent.core.models import ModelEventKind
from code_agent.core.errors import EngineLimitError


class BudgetedWindowClient:
    def __init__(self, model, sessions, current_thread, policy, limits, counter):
        self.model, self.sessions, self.current_thread = model, sessions, current_thread
        self.policy, self.limits, self.counter = policy, limits, counter

    def stream(self, system_prompt, messages, tools):
        return self.stream_for("main", system_prompt, messages, tools)

    async def stream_for(self, purpose, system_prompt, messages, tools):
        thread_id = self.current_thread()
        if not thread_id:
            raise ValueError("context request has no bound task")
        estimate = self.counter.request(system_prompt, messages, tools)
        if estimate > self.limits.input_cap(self.policy):
            raise ValueError("final input exceeds effective context cap")
        try:
            identifier = await self.sessions.reserve_context_call(
                thread_id, uuid.uuid4().hex, estimate + self.limits.output_tokens + self.policy.safety_tokens,
                self.policy.task_tokens, purpose,
            )
        except ValueError as error:
            if "budget" in str(error):
                raise EngineLimitError(str(error)) from None
            raise
        usage = None
        completed = False
        try:
            async for event in self.model.stream(system_prompt, messages, tools):
                if event.usage is not None:
                    usage = event.usage
                if event.kind is ModelEventKind.COMPLETED:
                    completed = True
                yield event
        finally:
            if usage is not None and usage.total_tokens > 0:
                await self.sessions.settle_context_call(thread_id, identifier, usage, estimate)
                if purpose == "handoff":
                    task = await self.sessions.load_task_for_thread(thread_id)
                    if task is not None:
                        await self.sessions.consume_task_usage(task.id, usage)
        if completed and (usage is None or usage.total_tokens == 0):
            raise RuntimeError("provider omitted token usage; reservation retained, accounting is uncertain")

    async def aclose(self):
        await self.model.aclose()
