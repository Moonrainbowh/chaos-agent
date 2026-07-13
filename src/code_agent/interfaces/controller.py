from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Optional, Protocol

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent
from code_agent.core.task import TaskRecord


class AgentRunner(Protocol):
    def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


class AgentController:
    """Expose one engine stream to interactive and non-interactive clients."""

    def __init__(self, engine: AgentRunner) -> None:
        if not hasattr(engine, "run"):
            raise TypeError("engine must provide run")
        self._engine = engine

    def replace_runner(self, engine: AgentRunner) -> None:
        """Replace the provider-bound runner only after the caller establishes idleness."""
        if not hasattr(engine, "run"):
            raise TypeError("engine must provide run")
        self._engine = engine

    async def ask(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]:
        kwargs = {"thread_id": thread_id, "cancellation": cancellation}
        if task is not None:
            kwargs["task"] = task
        async for event in self._engine.run(user_input, **kwargs):
            yield event

    async def resume(
        self,
        thread_id: str,
        user_input: str,
        *,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncIterator[AgentEvent]:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string")
        async for event in self.ask(
            user_input,
            thread_id=thread_id,
            cancellation=cancellation,
        ):
            yield event

    async def run_json(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncIterator[str]:
        async for event in self.ask(
            user_input,
            thread_id=thread_id,
            cancellation=cancellation,
        ):
            yield json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
