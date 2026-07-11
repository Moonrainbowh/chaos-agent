from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import (  # noqa: E402
    CancellationError,
    CancellationToken,
)


class CancellationTokenTests(unittest.TestCase):
    def test_cancel_is_idempotent_and_preserves_first_reason(self) -> None:
        token = CancellationToken()
        self.assertFalse(token.is_cancelled)
        self.assertIsNone(token.reason)

        self.assertTrue(token.cancel("user requested"))
        self.assertFalse(token.cancel("later reason"))

        self.assertTrue(token.is_cancelled)
        self.assertEqual(token.reason, "user requested")
        self.assertTrue(token.wait(timeout=0))

    def test_raise_if_cancelled_uses_dedicated_exception_and_reason(self) -> None:
        token = CancellationToken()
        token.raise_if_cancelled()
        token.cancel("time budget exhausted")

        with self.assertRaises(CancellationError) as raised:
            token.raise_if_cancelled()
        self.assertEqual(raised.exception.reason, "time budget exhausted")

    def test_cancel_can_wake_waiter_from_another_thread(self) -> None:
        token = CancellationToken()
        worker = Thread(target=token.cancel, args=("worker stopped",))
        worker.start()

        self.assertTrue(token.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())

    def test_cancel_rejects_blank_reason(self) -> None:
        token = CancellationToken()
        with self.assertRaises(ValueError):
            token.cancel(" ")


class AsyncCancellationTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_async_wakes_without_blocking_event_loop(self) -> None:
        token = CancellationToken()
        waiter = asyncio.create_task(token.wait_async(timeout=1.0))
        await asyncio.sleep(0)
        token.cancel("async stop")

        self.assertTrue(await waiter)

    async def test_wait_async_reports_timeout(self) -> None:
        token = CancellationToken()
        self.assertFalse(await token.wait_async(timeout=0.01))

    async def test_wait_async_timeout_does_not_use_thread_pool(self) -> None:
        token = CancellationToken()
        with patch(
            "asyncio.to_thread",
            side_effect=AssertionError("wait_async used the thread pool"),
        ):
            self.assertFalse(await token.wait_async(timeout=0.01))
        self.assertFalse(token._waiters)

    async def test_wait_async_token_cancellation_does_not_use_thread_pool(
        self,
    ) -> None:
        token = CancellationToken()
        with patch(
            "asyncio.to_thread",
            side_effect=AssertionError("wait_async used the thread pool"),
        ):
            waiter = asyncio.create_task(token.wait_async())
            await asyncio.sleep(0)
            token.cancel("async stop")
            self.assertTrue(await waiter)
        self.assertFalse(token._waiters)

    async def test_wait_async_can_be_woken_from_another_thread(self) -> None:
        token = CancellationToken()
        waiter = asyncio.create_task(token.wait_async())
        await asyncio.sleep(0)

        worker = Thread(target=token.cancel, args=("thread stop",))
        worker.start()
        try:
            self.assertTrue(await asyncio.wait_for(waiter, timeout=1.0))
        finally:
            worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())

    async def test_cancelled_async_waiters_are_removed(self) -> None:
        token = CancellationToken()
        waiters = [
            asyncio.create_task(token.wait_async()) for _ in range(4)
        ]
        await asyncio.sleep(0)

        for waiter in waiters:
            waiter.cancel()
        results = await asyncio.gather(*waiters, return_exceptions=True)

        try:
            self.assertTrue(
                all(isinstance(result, asyncio.CancelledError) for result in results)
            )
            self.assertFalse(token._waiters)
        finally:
            token.cancel("test cleanup")


if __name__ == "__main__":
    unittest.main()
