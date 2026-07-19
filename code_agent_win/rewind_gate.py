from __future__ import annotations

import asyncio
import math
import numbers
import sqlite3
import time
from pathlib import Path
from typing import TypeVar

from code_agent.sessions.rewind_models import CoverageToken


_Result = TypeVar("_Result")
_BUSY_CODES = frozenset({5, 6})  # SQLite SQLITE_BUSY and SQLITE_LOCKED.
_MAX_BUSY_TIMEOUT_MS = 2_147_483_647


class WorkspaceGateTimeout(TimeoutError):
    """Report bounded contention for a workspace mutation gate."""


class WorkspaceMutationGate:
    """Serialize one workspace's mutations across tasks and processes."""

    def __init__(
        self,
        product_state_root: Path,
        workspace_fingerprint: str,
    ) -> None:
        if not isinstance(product_state_root, Path):
            raise TypeError("product_state_root must be a Path")
        fingerprint = CoverageToken(workspace_fingerprint, 1).workspace_fingerprint
        self.path = (
            product_state_root
            / "rewind"
            / fingerprint
            / "mutation-gate.sqlite3"
        )
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        timeout_s: float = 5.0,
    ) -> WorkspaceGateLease:
        timeout = _timeout(timeout_s)
        deadline = time.monotonic() + timeout
        try:
            await asyncio.wait_for(
                self._lock.acquire(),
                _remaining(deadline),
            )
        except asyncio.TimeoutError:
            raise WorkspaceGateTimeout("workspace mutation gate timed out") from None
        remaining = _remaining(deadline)
        if remaining <= 0:
            self._lock.release()
            raise WorkspaceGateTimeout("workspace mutation gate timed out")
        worker = asyncio.create_task(
            asyncio.to_thread(_open_transaction, self.path, deadline)
        )
        try:
            connection = await asyncio.shield(worker)
        except asyncio.CancelledError:
            await self._cancelled_open(worker)
            raise
        except BaseException:
            self._lock.release()
            raise
        return WorkspaceGateLease(connection, self._lock)

    async def _cancelled_open(
        self,
        worker: asyncio.Task[sqlite3.Connection],
    ) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = await _settle(worker)
        except BaseException:
            pass
        try:
            if connection is not None:
                closer = asyncio.create_task(
                    asyncio.to_thread(_rollback_close, connection)
                )
                try:
                    await _settle(closer)
                except BaseException:
                    pass
        finally:
            self._lock.release()


class WorkspaceGateLease:
    """Own one independent SQLite transaction until idempotent release."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        instance_lock: asyncio.Lock,
    ) -> None:
        self._connection = connection
        self._instance_lock = instance_lock
        self._release_lock = asyncio.Lock()
        self._released = False

    async def release(self) -> None:
        async with self._release_lock:
            if self._released:
                return
            worker = asyncio.create_task(
                asyncio.to_thread(_rollback_close, self._connection)
            )
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await _settle(worker)
                except BaseException:
                    pass
                finally:
                    self._finish_release()
                raise
            except BaseException:
                self._finish_release()
                raise
            self._finish_release()

    def _finish_release(self) -> None:
        if self._released:
            return
        self._released = True
        self._instance_lock.release()


async def _settle(task: asyncio.Task[_Result]) -> _Result:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError("timeout_s must be a real number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_s must be finite and positive")
    return timeout


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _open_transaction(
    path: Path,
    deadline: float,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise WorkspaceGateTimeout("workspace mutation gate timed out")
        sqlite_timeout = min(
            remaining,
            _MAX_BUSY_TIMEOUT_MS / 1000,
        )
        connection = sqlite3.connect(
            path,
            timeout=sqlite_timeout,
            isolation_level=None,
            check_same_thread=False,
        )
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise WorkspaceGateTimeout("workspace mutation gate timed out")
        bounded = min(remaining, _MAX_BUSY_TIMEOUT_MS / 1000)
        timeout_ms = min(
            _MAX_BUSY_TIMEOUT_MS,
            max(1, math.ceil(bounded * 1000)),
        )
        connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.OperationalError as error:
        if connection is not None:
            connection.close()
        if _is_lock_contention(error):
            raise WorkspaceGateTimeout(
                "workspace mutation gate timed out"
            ) from error
        raise
    except BaseException:
        if connection is not None:
            connection.close()
        raise


def _rollback_close(connection: sqlite3.Connection) -> None:
    failure: BaseException | None = None
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except BaseException as error:
        failure = error
    try:
        connection.close()
    except BaseException as error:
        if failure is None:
            failure = error
    if failure is not None:
        raise failure


def _is_lock_contention(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in _BUSY_CODES:
        return True
    return error.args in (
        ("database is locked",),
        ("database table is locked",),
    )


__all__ = [
    "WorkspaceGateLease",
    "WorkspaceGateTimeout",
    "WorkspaceMutationGate",
]
