"""Guard every main and auxiliary request against the same durable budget."""
import uuid
from dataclasses import replace

from code_agent.core.models import ModelEventKind
from code_agent.core.errors import EngineLimitError
from .policy import RequestBudgetConstraints


class RequestCapacityError(ValueError):
    """Prepared input cannot fit the effective frozen request ceiling."""


class BudgetedWindowClient:
    accounts_task_usage = True
    def __init__(self, model, sessions, current_thread, policy, limits, counter, *, constraints=None):
        self.model, self.sessions, self.current_thread = model, sessions, current_thread
        self.policy, self.limits, self.counter = policy, limits, counter
        self.constraints = constraints or RequestBudgetConstraints()
        self.request_diagnostics = ()
        self.request_estimate_kind = "logical compatibility estimate"

    def stream(self, system_prompt, messages, tools):
        return self.stream_for("main", system_prompt, messages, tools)

    def effective_input_cap(self, *, output_tokens=None, constraints=None):
        """Effective frozen ceiling; prepared output can further reduce it."""
        output = max(self.limits.output_tokens, output_tokens or 0)
        cap = replace(self.limits, output_tokens=output).input_cap(self.policy)
        cap = self.constraints.input_cap(cap, self.policy.safety_tokens, output)
        if constraints is not None:
            cap = constraints.input_cap(cap, self.policy.safety_tokens, output)
        return cap

    async def preflight_request(self, system_prompt, messages, tools, *, constraints=None):
        """Prepare/check a candidate without budget admission or a Provider call.

        Returns (prepared request or None for a compatibility model, input
        estimate, output reserve). Sending still freezes and checks its own
        request, so a stale preflight cannot authorize a changed payload.
        """
        prepare = getattr(self.model, "prepare_request", None)
        send = getattr(self.model, "stream_prepared", None)
        if callable(prepare) != callable(send):
            raise TypeError("model must provide both public prepared request methods")
        prepared = await prepare(system_prompt, messages, tools) if callable(prepare) else None
        if callable(prepare) and prepared is None:
            raise TypeError("model returned no prepared request")
        if prepared is not None:
            estimate = self.counter.prepared(prepared)
            output_tokens = max(self.limits.output_tokens, prepared.max_output_tokens)
            self.request_diagnostics = prepared.diagnostics
            self.request_estimate_kind = "prepared-json-v1 local estimate"
        else:
            estimate = self.counter.request(system_prompt, messages, tools)
            output_tokens = self.limits.output_tokens
            self.request_diagnostics = ("logical compatibility model; no Provider serialization",)
            self.request_estimate_kind = "logical compatibility estimate"
        cap = self.effective_input_cap(output_tokens=output_tokens, constraints=constraints)
        if estimate > cap:
            raise RequestCapacityError("final input exceeds effective context cap")
        return prepared, estimate, output_tokens

    async def stream_for(self, purpose, system_prompt, messages, tools, *, constraints=None):
        thread_id = self.current_thread()
        if not thread_id:
            raise ValueError("context request has no bound task")
        prepared, estimate, output_tokens = await self.preflight_request(
            system_prompt, messages, tools, constraints=constraints)
        try:
            identifier = await self.sessions.reserve_context_call(
                thread_id, uuid.uuid4().hex, estimate + output_tokens + self.policy.safety_tokens,
                self.policy.task_tokens, purpose,
            )
        except ValueError as error:
            if "budget" in str(error):
                raise EngineLimitError(str(error)) from None
            raise
        usage = None
        completed = False
        try:
            events = self.model.stream_prepared(prepared) if prepared is not None else self.model.stream(system_prompt, messages, tools)
            try:
                async for event in events:
                    if event.usage is not None:
                        usage = event.usage
                    if event.kind is ModelEventKind.COMPLETED:
                        completed = True
                    yield event
            finally:
                closer = getattr(events, "aclose", None)
                if callable(closer):
                    await closer()
        finally:
            if usage is not None and usage.total_tokens > 0:
                await self.sessions.settle_context_call(thread_id, identifier, usage, estimate,
                    completed=completed)
        if completed and (usage is None or usage.total_tokens == 0):
            raise RuntimeError("provider omitted token usage; reservation retained, accounting is uncertain")

    async def aclose(self):
        await self.model.aclose()
