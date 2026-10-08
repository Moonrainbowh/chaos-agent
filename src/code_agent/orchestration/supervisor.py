from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Protocol

from code_agent.core.cancellation import CancellationError, CancellationToken

from .budget import BudgetExceededError, BudgetLedger
from .models import (
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
        task = self.start(request)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.cancel(request.run_id, "caller cancelled")
            return await _await_finished(task)

    async def _run(
        self, request: ChildRunRequest, cancellation: CancellationToken
    ) -> ChildRunResult:
        if not isinstance(request, ChildRunRequest):
            raise TypeError("request must be a ChildRunRequest")
        lease = None
        self._views[request.run_id] = RunView.from_request(request, RunStatus.QUEUED)
        self._notify(self._views[request.run_id])
        parent_watch = asyncio.create_task(
            self._propagate_parent_cancellation(cancellation)
        )
        try:
            cancellation.raise_if_cancelled()
            lease = await self._ledger.reserve(request)
            self._parent_cancellation.raise_if_cancelled()
            cancellation.raise_if_cancelled()
            result = await self._execute(request, cancellation)
            if result.run_id != request.run_id:
                raise ValueError("runner returned a mismatched run_id")
            try:
                await self._ledger.settle(lease, result.usage)
            except BudgetExceededError as error:
                result = replace(
                    result, status=RunStatus.FAILED, error=str(error), result=None
                )
            await self._set_result(result)
            return result
        except (CancellationError, asyncio.CancelledError) as error:
            if lease is not None:
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
            if lease is not None:
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
        if not isinstance(request, ChildRunRequest):
            raise TypeError("request must be a ChildRunRequest")
        if request.run_id in self._views or request.run_id in self._tokens:
            raise ValueError("run_id is already registered")
        cancellation = CancellationToken()
        self._tokens[request.run_id] = cancellation
        task = asyncio.create_task(self._run(request, cancellation))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(
            lambda _: self._tokens.pop(request.run_id, None)
        )
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
        started = asyncio.Event()
        runner = asyncio.create_task(self._run_agent(request, cancellation, started))
        stopped = asyncio.create_task(cancellation.wait_async())
        active = asyncio.create_task(started.wait())
        try:
            await asyncio.wait(
                (runner, stopped, active), return_when=asyncio.FIRST_COMPLETED
            )
            done, _ = await asyncio.wait(
                (runner, stopped), timeout=request.active_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runner not in done:
                cancellation.cancel("child active-time budget exceeded")
                runner.cancel()
            try:
                result = await asyncio.shield(runner)
            except asyncio.CancelledError:
                cancellation.raise_if_cancelled()
                raise
            if cancellation.is_cancelled:
                return _cancelled_result(result, cancellation)
            return result
        except asyncio.CancelledError:
            cancellation.cancel("child execution cancelled")
            runner.cancel()
            try:
                return _cancelled_result(await _await_finished(runner), cancellation)
            except asyncio.CancelledError:
                cancellation.raise_if_cancelled()
                raise
        finally:
            stopped.cancel()
            active.cancel()
            if not runner.done():
                cancellation.cancel("child execution stopped")
                runner.cancel()
            await asyncio.gather(runner, stopped, active, return_exceptions=True)

    async def _run_agent(
        self, request: ChildRunRequest, cancellation: CancellationToken,
        started: asyncio.Event,
    ) -> ChildRunResult:
        async with self._concurrency:
            cancellation.raise_if_cancelled()
            await self._set_status(request.run_id, RunStatus.RUNNING)
            started.set()
            return await self._run_with_write_lock(request, cancellation)

    async def _run_with_write_lock(
        self, request: ChildRunRequest, cancellation: CancellationToken
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


def _cancelled_result(
    result: ChildRunResult, cancellation: CancellationToken
) -> ChildRunResult:
    return replace(
        result, status=RunStatus.CANCELLED,
        error=cancellation.reason or "cancelled", result=None,
    )


async def _await_finished(task: asyncio.Task[ChildRunResult]) -> ChildRunResult:
    """Keep repeated caller cancellation from interrupting child cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    return task.result()
