from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Optional, Protocol

from code_agent.core.attachments import AttachmentRef, freeze_attachments
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
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]: ...

    def run_peer(
        self,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
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
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        kwargs = {"thread_id": thread_id, "cancellation": cancellation}
        if task is not None:
            kwargs["task"] = task
        checked = freeze_attachments(tuple(attachments))
        if checked:
            kwargs["attachments"] = checked
        async for event in self._engine.run(user_input, **kwargs):
            yield event

    async def resume(
        self,
        thread_id: str,
        user_input: str,
        *,
        cancellation: Optional[CancellationToken] = None,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string")
        async for event in self.ask(
            user_input,
            thread_id=thread_id,
            cancellation=cancellation,
            attachments=attachments,
        ):
            yield event

    async def receive_peer(
        self,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Wake the runner for queued peer context without user impersonation."""
        run_peer = getattr(self._engine, "run_peer", None)
        if not callable(run_peer):
            raise RuntimeError("peer-triggered turns are unavailable")
        async for event in run_peer(
            thread_id=thread_id, cancellation=cancellation
        ):
            yield event

    async def run_json(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[str]:
        async for event in self.ask(
            user_input,
            thread_id=thread_id,
            cancellation=cancellation,
            attachments=attachments,
        ):
            yield json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
