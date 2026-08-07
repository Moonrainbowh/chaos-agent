from __future__ import annotations

from collections.abc import AsyncIterator

from code_agent.core.attachments import AttachmentRef
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent


class FakeEngine:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self.events = events
        self.calls: list[tuple[str, str | None, CancellationToken | None]] = []

    async def run(
        self,
        user_input: str,
        *,
        thread_id: str | None = None,
        cancellation: CancellationToken | None = None,
        attachments: tuple[AttachmentRef, ...] = (),
        **_: object,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append((user_input, thread_id, cancellation))
        self.attachments = attachments
        for event in self.events:
            yield event
