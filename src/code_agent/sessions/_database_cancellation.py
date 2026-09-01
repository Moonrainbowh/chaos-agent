from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import TypeVar, cast


_Result = TypeVar("_Result")
_BUSY_CODES = frozenset({5, 6})  # SQLite SQLITE_BUSY and SQLITE_LOCKED.
_BUSY_SLICE_MS = 50
_NO_COMMITTED_RESULT = object()


class DatabaseOperationCancelled(Exception):
    """Internal signal that a transaction must roll back before cancellation."""


class _CallAdmission:
    """Prevent a cancelled queued worker from entering database code later."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._cancelled = False

    def invoke(
        self,
        call: Callable[[DatabaseCancellation], _Result],
        cancellation: DatabaseCancellation,
    ) -> _Result:
        with self._lock:
            if self._cancelled:
                return cast(_Result, None)
            self._started = True
        return call(cancellation)

    def cancel_before_start(self) -> bool:
        with self._lock:
            if self._started:
                return False
            self._cancelled = True
            return True


class DatabaseCancellation:
    """Coordinate an asyncio cancellation with one SQLite worker thread."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._committed = threading.Event()
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._commit_started = False
        self._committed_result: object = _NO_COMMITTED_RESULT

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def committed(self) -> bool:
        return self._committed.is_set()

    def attach(self, connection: sqlite3.Connection) -> None:
        with self._lock:
            self._connection = connection
            if self.cancelled:
                connection.interrupt()

    def detach(self, connection: sqlite3.Connection) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            connection = None if self._commit_started else self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass

    def check(self) -> None:
        if self.cancelled:
            raise DatabaseOperationCancelled

    def progress(self) -> int:
        return int(self.cancelled)

    def commit(
        self,
        connection: sqlite3.Connection,
        result: object = _NO_COMMITTED_RESULT,
    ) -> None:
        with self._lock:
            self.check()
            self._commit_started = True
        connection.execute("COMMIT")
        self._committed_result = result
        self._committed.set()

    def result_after_commit(self) -> object:
        if not self.committed or self._committed_result is _NO_COMMITTED_RESULT:
            raise LookupError("committed database result is unavailable")
        return self._committed_result


def rollback_database_transaction(
    connection: sqlite3.Connection | None,
    primary: BaseException,
) -> bool:
    try:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        return True
    except BaseException as cleanup:
        _add_cleanup_note(primary, "rollback", cleanup)
        return False


def cleanup_database_connection(
    cancellation: DatabaseCancellation,
    connection: sqlite3.Connection,
    primary: BaseException | None,
) -> None:
    failures: list[tuple[str, BaseException]] = []
    try:
        cancellation.detach(connection)
    except BaseException as error:
        failures.append(("detach", error))
    try:
        connection.close()
    except BaseException as error:
        failures.append(("close", error))
    if not failures:
        return
    if primary is not None:
        for operation, error in failures:
            _add_cleanup_note(primary, operation, error)
        return
    if cancellation.committed:
        return
    raise failures[0][1]


def _add_cleanup_note(
    primary: BaseException,
    operation: str,
    cleanup: BaseException,
) -> None:
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(
            f"SQLite {operation} cleanup failed: "
            f"{type(cleanup).__name__}: {cleanup}"
        )


def begin_cancellable_transaction(
    connection: sqlite3.Connection,
    *,
    write: bool,
    cancellation: DatabaseCancellation,
    timeout_ms: int,
) -> None:
    """Acquire a transaction lock in short slices so cancellation stays prompt."""
    cancellation.check()
    deadline = time.monotonic() + timeout_ms / 1_000
    while True:
        cancellation.check()
        remaining = deadline - time.monotonic()
        slice_ms = min(_BUSY_SLICE_MS, max(1, int(remaining * 1_000)))
        connection.execute(f"PRAGMA busy_timeout = {slice_ms}")
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not write:
                connection.execute(
                    "SELECT rootpage FROM sqlite_master LIMIT 1"
                ).fetchone()
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_SLICE_MS}")
            return
        except sqlite3.OperationalError as error:
            if not write and not rollback_database_transaction(connection, error):
                raise
            if not _is_lock_contention(error):
                raise
            cancellation.check()
            if time.monotonic() >= deadline:
                raise


def _is_lock_contention(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in _BUSY_CODES:
        return True
    return error.args in (
        ("database is locked",),
        ("database table is locked",),
    )


async def run_cancellable_database_call(
    call: Callable[[DatabaseCancellation], _Result],
) -> _Result:
    cancellation = DatabaseCancellation()
    admission = _CallAdmission()
    worker = asyncio.create_task(
        asyncio.to_thread(admission.invoke, call, cancellation)
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        if admission.cancel_before_start():
            worker.cancel()
            raise cancelled
        cancellation.cancel()
        try:
            result = await _settle(worker)
        except DatabaseOperationCancelled:
            pass
        except BaseException as error:
            if cancellation.committed:
                try:
                    return cast(_Result, cancellation.result_after_commit())
                except LookupError:
                    pass
            add_note = getattr(cancelled, "add_note", None)
            if callable(add_note):
                add_note(
                    "SQLite cancellation cleanup failed: "
                    f"{type(error).__name__}: {error}"
                )
        else:
            if cancellation.committed:
                return result
        raise cancelled


async def _settle(task: asyncio.Task[_Result]) -> _Result:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
