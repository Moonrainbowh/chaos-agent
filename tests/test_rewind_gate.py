from __future__ import annotations

import asyncio
import functools
import math
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from code_agent_win.rewind_gate import (
    WorkspaceGateTimeout,
    WorkspaceMutationGate,
)


_FINGERPRINT = "a" * 64
_HOLDER = """
import asyncio
import sys
from pathlib import Path
from code_agent_win.rewind_gate import WorkspaceMutationGate

async def main():
    gate = WorkspaceMutationGate(Path(sys.argv[1]), sys.argv[2])
    lease = await gate.acquire(timeout_s=2.0)
    print("READY", flush=True)
    await asyncio.to_thread(sys.stdin.buffer.read, 1)
    await lease.release()

asyncio.run(main())
"""


class WorkspaceMutationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_two_gate_instances_are_mutually_exclusive(self) -> None:
        first = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        second = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await first.acquire(timeout_s=1.0)
        try:
            with self.assertRaises(WorkspaceGateTimeout):
                await second.acquire(timeout_s=0.05)
        finally:
            await lease.release()

    async def test_subprocess_holder_causes_bounded_timeout(self) -> None:
        process = await self._holder()
        try:
            gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
            with self.assertRaises(WorkspaceGateTimeout):
                await gate.acquire(timeout_s=0.05)
        finally:
            self._stop_holder(process)

    async def test_same_instance_holder_causes_bounded_timeout(self) -> None:
        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await gate.acquire(timeout_s=1.0)
        try:
            with self.assertRaises(WorkspaceGateTimeout):
                await gate.acquire(timeout_s=0.05)
        finally:
            await lease.release()

    async def test_release_allows_next_holder(self) -> None:
        first = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        second = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await first.acquire()
        await lease.release()
        next_lease = await second.acquire(timeout_s=1.0)
        await next_lease.release()

    async def test_crashed_holder_is_released_by_sqlite(self) -> None:
        process = await self._holder()
        self._stop_holder(process)
        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await gate.acquire(timeout_s=1.0)
        await lease.release()

    async def test_different_fingerprints_do_not_block(self) -> None:
        first = WorkspaceMutationGate(self.state_root, "a" * 64)
        second = WorkspaceMutationGate(self.state_root, "b" * 64)
        first_lease = await first.acquire(timeout_s=1.0)
        second_lease = await second.acquire(timeout_s=1.0)
        await asyncio.gather(first_lease.release(), second_lease.release())

    async def test_release_may_run_on_a_different_worker_thread(self) -> None:
        executors = (ThreadPoolExecutor(1), ThreadPoolExecutor(1))
        calls = iter(range(20))

        async def alternating(function, *args):
            loop = asyncio.get_running_loop()
            executor = executors[next(calls) % 2]
            return await loop.run_in_executor(
                executor, functools.partial(function, *args)
            )

        try:
            with patch("code_agent_win.rewind_gate.asyncio.to_thread", alternating):
                gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
                lease = await gate.acquire()
                await lease.release()
                reacquired = await gate.acquire()
                await reacquired.release()
        finally:
            for executor in executors:
                executor.shutdown(wait=True)

    async def test_failed_acquire_releases_the_in_process_lock(self) -> None:
        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        with patch(
            "code_agent_win.rewind_gate._open_transaction",
            side_effect=RuntimeError("failed open"),
        ):
            with self.assertRaises(RuntimeError):
                await gate.acquire(timeout_s=1.0)
        lease = await gate.acquire(timeout_s=1.0)
        await lease.release()

    async def test_cancelled_acquire_settles_worker_and_leaks_no_lock(self) -> None:
        started = threading.Event()
        proceed = threading.Event()
        from code_agent_win import rewind_gate

        original = rewind_gate._open_transaction

        def delayed(*args):
            started.set()
            proceed.wait(5)
            return original(*args)

        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        with patch("code_agent_win.rewind_gate._open_transaction", delayed):
            acquiring = asyncio.create_task(gate.acquire(timeout_s=5.0))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            acquiring.cancel()
            await asyncio.sleep(0)
            self.assertFalse(acquiring.done())
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await acquiring
        lease = await gate.acquire(timeout_s=1.0)
        await lease.release()

    async def test_granted_instance_lock_then_cancel_releases_ownership(self) -> None:
        original_wait = asyncio.wait

        async def cancel_after_grant(tasks, **kwargs):
            result = await original_wait(tasks, **kwargs)
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
            return result

        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        with patch("code_agent_win.rewind_gate.asyncio.wait", cancel_after_grant):
            with self.assertRaises(asyncio.CancelledError):
                await gate.acquire(timeout_s=1.0)
        lease = await gate.acquire(timeout_s=1.0)
        await lease.release()

    async def test_concurrent_and_double_release_are_idempotent(self) -> None:
        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await gate.acquire()
        await asyncio.gather(lease.release(), lease.release())
        await lease.release()
        reacquired = await gate.acquire(timeout_s=1.0)
        await reacquired.release()

    async def test_cancelled_release_settles_worker_before_unlock(self) -> None:
        from code_agent_win import rewind_gate

        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        lease = await gate.acquire()
        original = rewind_gate._rollback_close
        started = threading.Event()
        proceed = threading.Event()

        def delayed(connection):
            started.set()
            proceed.wait(5)
            original(connection)

        with patch("code_agent_win.rewind_gate._rollback_close", delayed):
            releasing = asyncio.create_task(lease.release())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            releasing.cancel()
            contender = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
            with self.assertRaises(WorkspaceGateTimeout):
                await contender.acquire(timeout_s=0.05)
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await releasing
        next_lease = await contender.acquire(timeout_s=1.0)
        await next_lease.release()

    async def test_invalid_fingerprint_and_timeout_fail_closed(self) -> None:
        invalid = ("", "A" * 64, "a" * 63, "../" + "a" * 64)
        for fingerprint in invalid:
            with self.subTest(fingerprint=fingerprint):
                with self.assertRaises((TypeError, ValueError)):
                    WorkspaceMutationGate(self.state_root, fingerprint)
        gate = WorkspaceMutationGate(self.state_root, _FINGERPRINT)
        for timeout in (True, False, 0, -1, math.nan, math.inf, "1", None):
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    await gate.acquire(timeout_s=timeout)  # type: ignore[arg-type]
        lease = await gate.acquire(timeout_s=1e308)
        await lease.release()

    def test_extended_busy_codes_are_lock_contention(self) -> None:
        from code_agent_win.rewind_gate import _is_lock_contention

        error = sqlite3.OperationalError("busy snapshot")
        error.sqlite_errorcode = 5 | (2 << 8)
        self.assertTrue(_is_lock_contention(error))

    async def _holder(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", _HOLDER, str(self.state_root), _FINGERPRINT],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            line = await asyncio.wait_for(
                asyncio.to_thread(process.stdout.readline),
                deadline - loop.time(),
            )
            self.assertEqual(line.strip(), b"READY")
            return process
        except BaseException:
            self._stop_holder(process)
            raise

    def _stop_holder(self, process: subprocess.Popen[bytes]) -> None:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
