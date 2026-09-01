from __future__ import annotations

import asyncio
import concurrent.futures
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._database import SessionDatabase  # noqa: E402
from code_agent.sessions._database_cancellation import (  # noqa: E402
    DatabaseCancellation,
)
from code_agent.sessions.errors import SessionStorageError  # noqa: E402


class _ConnectionBackedDatabase(SessionDatabase):
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def _connect(self) -> sqlite3.Connection:
        return self.connection  # type: ignore[return-value]


class _FaultingConnection:
    def __init__(
        self,
        *,
        fail_close: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.fail_close = fail_close
        self.fail_rollback = fail_rollback
        self.in_transaction = False
        self.commit_entered = threading.Event()
        self.release_commit = threading.Event()
        self.committed = False
        self.close_attempted = False
        self.rollback_attempted = False

    def set_progress_handler(self, handler: object, instructions: int) -> None:
        del handler, instructions

    def execute(self, statement: str) -> _FaultingConnection:
        if statement.startswith("PRAGMA busy_timeout"):
            return self
        if statement == "BEGIN IMMEDIATE":
            self.in_transaction = True
            return self
        if statement == "COMMIT":
            self.commit_entered.set()
            if not self.release_commit.wait(2.0):
                raise AssertionError("commit was not released")
            self.committed = True
            self.in_transaction = False
            return self
        if statement == "ROLLBACK":
            self.rollback_attempted = True
            if self.fail_rollback:
                raise sqlite3.OperationalError("injected rollback failure")
            self.in_transaction = False
            return self
        raise AssertionError(statement)

    def interrupt(self) -> None:
        return None

    def close(self) -> None:
        self.close_attempted = True
        if self.fail_close:
            raise OSError("injected close failure")


class DatabaseCancellationRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_read_lock_wait_returns_within_half_second(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite3"
            database = SessionDatabase(path)
            with closing(
                sqlite3.connect(path, isolation_level=None)
            ) as setup, setup:
                setup.execute("PRAGMA journal_mode = DELETE")

            blocker = sqlite3.connect(
                path,
                isolation_level=None,
                check_same_thread=False,
            )
            blocker.execute("BEGIN EXCLUSIVE")
            worker_connected = threading.Event()
            original_connect = database._connect

            def observed_connect() -> sqlite3.Connection:
                connection = original_connect()
                worker_connected.set()
                return connection

            database._connect = observed_connect  # type: ignore[method-assign]
            read = asyncio.create_task(
                database.read(
                    lambda connection: connection.execute(
                        "SELECT COUNT(*) FROM threads"
                    ).fetchone()
                )
            )
            connected = await asyncio.to_thread(worker_connected.wait, 1.0)
            self.assertTrue(connected)
            await asyncio.sleep(0.05)

            release_guard = threading.Lock()
            released = threading.Event()

            def release_blocker() -> None:
                with release_guard:
                    if released.is_set():
                        return
                    blocker.execute("ROLLBACK")
                    blocker.close()
                    released.set()

            safety_release = threading.Timer(1.0, release_blocker)
            safety_release.start()
            started = time.monotonic()
            read.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await read
            finally:
                safety_release.cancel()
                release_blocker()
                safety_release.join(1.0)

            self.assertLess(time.monotonic() - started, 0.5)

    async def test_cancelled_queued_worker_does_not_wait_for_default_executor(
        self,
    ) -> None:
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        occupier_started = threading.Event()
        release_occupier = threading.Event()

        def occupy_executor() -> None:
            occupier_started.set()
            release_occupier.wait(2.0)

        occupier = asyncio.create_task(asyncio.to_thread(occupy_executor))
        self.assertTrue(await _wait_for_event(occupier_started, 1.0))
        worker_started = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            database = SessionDatabase(Path(directory) / "sessions.sqlite3")

            def queued_operation(connection: sqlite3.Connection) -> int:
                worker_started.set()
                return int(connection.execute("SELECT 1").fetchone()[0])

            database_call = asyncio.create_task(database.read(queued_operation))
            await asyncio.sleep(0.05)
            self.assertFalse(worker_started.is_set())

            safety_release = threading.Timer(1.0, release_occupier.set)
            safety_release.start()
            started = time.monotonic()
            database_call.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await database_call
            finally:
                safety_release.cancel()
                release_occupier.set()
                safety_release.join(1.0)
                await occupier
                executor.shutdown(wait=True)

            self.assertLess(time.monotonic() - started, 0.5)
            self.assertFalse(worker_started.is_set())

    async def test_committed_result_survives_close_failure_during_cancel(
        self,
    ) -> None:
        connection = _FaultingConnection(fail_close=True)
        database = _ConnectionBackedDatabase(connection)

        result = await _cancel_during_commit(database, connection)

        self.assertEqual(result, "committed")
        self.assertTrue(connection.committed)
        self.assertTrue(connection.close_attempted)

    async def test_committed_result_survives_detach_failure_during_cancel(
        self,
    ) -> None:
        connection = _FaultingConnection()
        database = _ConnectionBackedDatabase(connection)

        def fail_detach(
            cancellation: DatabaseCancellation,
            attached: sqlite3.Connection,
        ) -> None:
            del cancellation, attached
            raise OSError("injected detach failure")

        with patch.object(DatabaseCancellation, "detach", fail_detach):
            result = await _cancel_during_commit(database, connection)

        self.assertEqual(result, "committed")
        self.assertTrue(connection.committed)
        self.assertTrue(connection.close_attempted)

    async def test_rollback_and_close_failures_preserve_primary_error(self) -> None:
        connection = _FaultingConnection(
            fail_close=True,
            fail_rollback=True,
        )
        database = _ConnectionBackedDatabase(connection)
        primary = SessionStorageError("primary operation failure")

        def fail_operation(attached: sqlite3.Connection) -> None:
            del attached
            raise primary

        with self.assertRaises(SessionStorageError) as raised:
            await database.write(fail_operation)

        self.assertIs(raised.exception, primary)
        self.assertTrue(connection.rollback_attempted)
        self.assertTrue(connection.close_attempted)

    async def test_write_lock_wait_still_succeeds_before_five_second_limit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite3"
            database = SessionDatabase(path)
            blocker = sqlite3.connect(
                path,
                isolation_level=None,
                check_same_thread=False,
            )
            blocker.execute("BEGIN IMMEDIATE")

            def release_blocker() -> None:
                blocker.execute("ROLLBACK")
                blocker.close()

            release = threading.Timer(0.15, release_blocker)
            release.start()
            started = time.monotonic()
            try:
                result = await database.write(lambda connection: "written")
            finally:
                release.join(1.0)

            self.assertEqual(result, "written")
            self.assertGreaterEqual(time.monotonic() - started, 0.1)


async def _wait_for_event(event: threading.Event, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not event.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return event.is_set()


async def _cancel_during_commit(
    database: SessionDatabase,
    connection: _FaultingConnection,
) -> str:
    call = asyncio.create_task(database.write(lambda attached: "committed"))
    entered = await asyncio.to_thread(connection.commit_entered.wait, 1.0)
    if not entered:
        raise AssertionError("commit was not entered")
    call.cancel()
    release = threading.Timer(0.05, connection.release_commit.set)
    release.start()
    try:
        return await call
    finally:
        release.join(1.0)


if __name__ == "__main__":
    unittest.main()
