from __future__ import annotations

import asyncio
import re
import math
import time
from dataclasses import dataclass
from typing import Mapping

from code_agent.core.cancellation import CancellationToken
from code_agent.policy.classifier import EDIT_PLAN_RISK_FLAGS


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_EDIT_PLAN_APPROVAL_DIFF_CHARS = 1_000_000
MAX_EDIT_PLAN_APPROVAL_PATHS = 32


@dataclass(frozen=True)
class EditPlanApprovalView:
    """Carry a bounded, locally trusted edit-plan preview to the Host UI."""

    plan_id: str
    plan_digest: str
    operation_summaries: tuple[str, ...]
    risk_flags: tuple[str, ...]
    paths: tuple[str, ...]
    combined_diff: str
    diff_truncated: bool

    def __post_init__(self) -> None:
        _bounded_text(self.plan_id, "plan_id", 256)
        if not _SHA256.fullmatch(self.plan_digest):
            raise ValueError("plan_digest must be a lowercase SHA-256")
        _bounded_text_tuple(
            self.operation_summaries, "operation_summaries", 32, 512, empty=False
        )
        _bounded_text_tuple(self.paths, "paths", MAX_EDIT_PLAN_APPROVAL_PATHS, 1_024, empty=False)
        _bounded_text_tuple(self.risk_flags, "risk_flags", len(EDIT_PLAN_RISK_FLAGS), 64)
        if set(self.risk_flags).difference(EDIT_PLAN_RISK_FLAGS):
            raise ValueError("risk_flags contain an unknown edit-plan risk")
        if not isinstance(self.combined_diff, str):
            raise TypeError("combined_diff must be text")
        if len(self.combined_diff) > MAX_EDIT_PLAN_APPROVAL_DIFF_CHARS:
            raise ValueError("combined_diff is too large")
        if not isinstance(self.diff_truncated, bool):
            raise TypeError("diff_truncated must be a bool")


def _bounded_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip() or len(value) > maximum or any(char in value for char in "\0\r\n"):
        raise ValueError(f"{name} must be bounded single-line text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be strict UTF-8 text") from error


def _bounded_text_tuple(
    value: object,
    name: str,
    maximum_items: int,
    maximum_chars: int,
    *,
    empty: bool = True,
) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if (not empty and not value) or len(value) > maximum_items:
        raise ValueError(f"{name} has an invalid item count")
    for item in value:
        _bounded_text(item, name, maximum_chars)
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    name: str
    arguments: Mapping[str, object]
    risk: str | None = None
    target: str | None = None
    reason: str | None = None
    edit_plan: EditPlanApprovalView | None = None

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
        if self.edit_plan is not None and not isinstance(
            self.edit_plan, EditPlanApprovalView
        ):
            raise TypeError("edit_plan must be an EditPlanApprovalView or None")
        if self.edit_plan is not None:
            if self.name != "apply_workspace_edit_plan_v1":
                raise ValueError("edit_plan is valid only for edit-plan apply approval")
            if (
                self.arguments.get("plan_id") != self.edit_plan.plan_id
                or self.arguments.get("plan_digest") != self.edit_plan.plan_digest
            ):
                raise ValueError("edit_plan identity must match approval arguments")


class ApprovalBroker:
    """Bridge a policy dispatcher and an interactive approval surface."""

    def __init__(self, repository=None, ttl_seconds=300, clock=time.time) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 3600:
            raise ValueError("approval TTL must be positive and at most one hour")
        if not callable(clock):
            raise TypeError("clock must be callable")
        from .approval_persistence import ApprovalPersistence
        self._persistence = ApprovalPersistence(repository, ttl_seconds, clock)
        self._requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def request(
        self, request: ApprovalRequest, cancellation: CancellationToken, *,
        execution_context=None, workspace_root=None,
    ) -> bool:
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        cancellation.raise_if_cancelled()
        if request.request_id in self._pending:
            raise ValueError("approval request id is already pending")
        decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.request_id] = decision
        live = None
        cancelled = None
        try:
            live = await self._persistence.register(request, decision, execution_context, workspace_root)
            cancellation.raise_if_cancelled()
            await self._requests.put(request)
            cancelled = asyncio.create_task(cancellation.wait_async())
            done, _ = await asyncio.wait(
                (decision, cancelled), return_when=asyncio.FIRST_COMPLETED,
                timeout=self._persistence.ttl_seconds if live is not None else None,
            )
            if cancelled in done:
                cancellation.raise_if_cancelled()
            if decision not in done:
                return False
            approved = decision.result()
            if live is not None and not live.committed:
                async with self._persistence.lock:
                    await self._persistence.consume(live, approved)
            return approved
        finally:
            self._pending.pop(request.request_id, None)
            if not decision.done():
                decision.cancel()
            if cancelled is not None:
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)
            await self._persistence.cleanup(live)

    async def pending(self, task_id: str):
        """Read bounded durable cards without consuming the local UI queue."""
        return await self._persistence.pending(task_id)

    async def respond(self, request_id: str, **response):
        """Authenticate and consume a bound decision before waking its live waiter."""
        return await self._persistence.respond(request_id, **response)

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
