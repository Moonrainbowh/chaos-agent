from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping

from code_agent.core.cancellation import CancellationToken


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    name: str
    arguments: Mapping[str, object]
    risk: str | None = None
    target: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-blank text")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be non-blank text")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        for field_name in ("risk", "target", "reason"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-blank text or None")


class ApprovalBroker:
    """Bridge a policy dispatcher and an interactive approval surface."""

    def __init__(self) -> None:
        self._requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def request(
        self, request: ApprovalRequest, cancellation: CancellationToken
    ) -> bool:
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        cancellation.raise_if_cancelled()
        if request.request_id in self._pending:
            raise ValueError("approval request id is already pending")
        decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.request_id] = decision
        await self._requests.put(request)
        cancelled = asyncio.create_task(cancellation.wait_async())
        try:
            done, _ = await asyncio.wait(
                (decision, cancelled), return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done:
                cancellation.raise_if_cancelled()
            return decision.result()
        finally:
            self._pending.pop(request.request_id, None)
            cancelled.cancel()

    async def next_request(self) -> ApprovalRequest:
        while True:
            request = await self._requests.get()
            if request.request_id in self._pending:
                return request

    def resolve(self, request_id: str, approved: bool) -> bool:
        if not isinstance(request_id, str) or not isinstance(approved, bool):
            raise TypeError("request_id and approved must be text and bool")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True
