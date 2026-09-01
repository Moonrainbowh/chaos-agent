from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._database_cancellation import (  # noqa: E402
    run_cancellable_database_call,
)


class _GatedCommitConnection:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.interrupts = 0

    def execute(self, statement: str) -> None:
        if statement != "COMMIT":
            raise AssertionError(statement)
        self.entered.set()
        if not self.release.wait(1):
            raise AssertionError("commit was not released")

    def interrupt(self) -> None:
        self.interrupts += 1


class DatabaseCancellationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_that_won_the_race_returns_success_not_cancellation(self) -> None:
        connection = _GatedCommitConnection()

        def call(cancellation) -> str:
            cancellation.attach(connection)  # type: ignore[arg-type]
            try:
                cancellation.commit(connection)  # type: ignore[arg-type]
                return "committed"
            finally:
                cancellation.detach(connection)  # type: ignore[arg-type]

        task = asyncio.create_task(run_cancellable_database_call(call))
        entered = await asyncio.to_thread(connection.entered.wait, 1)
        self.assertTrue(entered)
        task.cancel()
        timer = threading.Timer(0.05, connection.release.set)
        timer.start()
        try:
            result = await task
        finally:
            timer.join(1)

        self.assertEqual(result, "committed")
        self.assertEqual(connection.interrupts, 0)


if __name__ == "__main__":
    unittest.main()
