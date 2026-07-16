from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Protocol

from code_agent.core.cancellation import CancellationError, CancellationToken

from .budget import BudgetExceededError, BudgetLedger
from .models import (
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
    RunView,
)


class ChildAgentRunner(Protocol):
    async def run(
        self,
        request: ChildRunRequest,
        cancellation: CancellationToken,
    ) -> ChildRunResult: ...


class ChildRunSupervisor:
    def __init__(
        self,
        runner: ChildAgentRunner,
        ledger: BudgetLedger,
        parent_cancellation: CancellationToken | None = None,
    ) -> None:
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("ledger must be a BudgetLedger")
        self._runner = runner
        self._ledger = ledger
        self._parent_cancellation = parent_cancellation or CancellationToken()
        self._concurrency = asyncio.Semaphore(ledger.budget.max_concurrency)
        self._write_lock = asyncio.Lock()
        self._tokens: dict[str, CancellationToken] = {}
        self._views: dict[str, RunView] = {}
        self._tasks: set[asyncio.Task[ChildRunResult]] = set()
        self._lock = asyncio.Lock()
        self._listeners: set[Callable[[RunView], None]] = set()

    def subscribe(self, listener: Callable[[RunView], None]) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def run(self, request: ChildRunRequest) -> ChildRunResult:
        if not isinstance(request, ChildRunRequest):
            raise TypeError("request must be a ChildRunRequest")
        lease = await self._ledger.reserve(request)
        cancellation = CancellationToken()
        async with self._lock:
            if request.run_id in self._views:
                await self._ledger.release(lease)
                raise ValueError("run_id is already registered")
            self._tokens[request.run_id] = cancellation
            self._views[request.run_id] = RunView.from_request(request, RunStatus.QUEUED)
            queued = self._views[request.run_id]
        self._notify(queued)
        parent_watch = asyncio.create_task(
            self._propagate_parent_cancellation(cancellation)
        )
        try:
            async with self._concurrency:
                self._parent_cancellation.raise_if_cancelled()
                cancellation.raise_if_cancelled()
                await self._set_status(request.run_id, RunStatus.RUNNING)
                result = await self._execute(request, cancellation)
            if result.run_id != request.run_id:
                raise ValueError("runner returned a mismatched run_id")
            try:
                await self._ledger.settle(lease, result.usage)
            except BudgetExceededError as error:
                result = ChildRunResult(
                    request.run_id,
                    RunStatus.FAILED,
                    "",
                    AgentUsage(
                        min(result.usage.total_tokens, lease.token_budget),
                        min(result.usage.tool_calls, lease.tool_budget),
                        min(result.usage.active_seconds, lease.active_seconds),
                    ),
                    error=str(error),
                )
            await self._set_result(result)
            return result
        except (CancellationError, asyncio.CancelledError) as error:
            await self._ledger.release(lease)
            result = ChildRunResult(
                request.run_id,
                RunStatus.CANCELLED,
                "",
                error=str(error) or "cancelled",
            )
            await self._set_result(result)
            return result
        except Exception as error:
            await self._ledger.release(lease)
            result = ChildRunResult(
                request.run_id,
                RunStatus.FAILED,
                "",
                error=_safe_error(error),
            )
            await self._set_result(result)
            return result
        finally:
            parent_watch.cancel()
            await asyncio.gather(parent_watch, return_exceptions=True)
            async with self._lock:
                self._tokens.pop(request.run_id, None)

    def start(self, request: ChildRunRequest) -> asyncio.Task[ChildRunResult]:
        task = asyncio.create_task(self.run(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel(self, run_id: str, reason: str = "cancelled") -> bool:
        async with self._lock:
            token = self._tokens.get(run_id)
        return False if token is None else token.cancel(reason)

    async def cancel_all(self, reason: str = "parent cancelled") -> int:
        async with self._lock:
            tokens = tuple(self._tokens.values())
        return sum(1 for token in tokens if token.cancel(reason))

    async def wait_all(self) -> tuple[ChildRunResult, ...]:
        tasks = tuple(self._tasks)
        if not tasks:
            return ()
        return tuple(await asyncio.gather(*tasks))

    async def views(self) -> tuple[RunView, ...]:
        async with self._lock:
            return tuple(self._views.values())

    async def _execute(
        self,
        request: ChildRunRequest,
        cancellation: CancellationToken,
    ) -> ChildRunResult:
        if request.agent.may_write:
            async with self._write_lock:
                cancellation.raise_if_cancelled()
                return await self._runner.run(request, cancellation)
        return await self._runner.run(request, cancellation)

    async def _propagate_parent_cancellation(
        self,
        child: CancellationToken,
    ) -> None:
        await self._parent_cancellation.wait_async()
        child.cancel(self._parent_cancellation.reason or "parent cancelled")

    async def _set_status(self, run_id: str, status: RunStatus) -> None:
        async with self._lock:
            view = self._views[run_id]
            self._views[run_id] = replace(view, status=status)
            updated = self._views[run_id]
        self._notify(updated)

    async def _set_result(self, result: ChildRunResult) -> None:
        async with self._lock:
            self._views[result.run_id] = self._views[result.run_id].with_result(result)
            updated = self._views[result.run_id]
        self._notify(updated)

    def _notify(self, view: RunView) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(view)
            except Exception:
                continue


def _safe_error(error: Exception) -> str:
    name = type(error).__name__
    return name if name else "child run failed"
