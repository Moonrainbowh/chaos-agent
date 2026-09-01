from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _windows_process, _windows_spawn  # noqa: E402
from code_agent.runtime.errors import ProcessTreeTerminationError  # noqa: E402
from code_agent.runtime.tests._windows_job_support import (  # noqa: E402
    FakeLease,
    FakeProcess,
    RecordingJob,
)


class WindowsJobCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_cleanup_failure_wins_cancellation_race(self) -> None:
        process = FakeProcess()
        process_wait = asyncio.create_task(process.wait())
        failure = ProcessTreeTerminationError(process.pid, ("cleanup failed",))
        original_gather = asyncio.gather

        async def fail_cleanup(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise failure

        async def cancel_after_done(task: asyncio.Task[None]) -> None:
            await original_gather(task, return_exceptions=True)
            raise asyncio.CancelledError

        try:
            with patch.object(
                _windows_process, "terminate_process_tree", fail_cleanup
            ), patch.object(
                _windows_process.asyncio, "shield", cancel_after_done
            ):
                with self.assertRaises(ProcessTreeTerminationError) as raised:
                    await _windows_process.complete_process_termination(
                        process, process_wait, object(), object()
                    )
        finally:
            process.complete(-9)
            await process_wait

        self.assertIs(raised.exception, failure)
        self.assertIsInstance(raised.exception.__cause__, asyncio.CancelledError)

    async def test_start_failure_cleanup_survives_external_cancellation(self) -> None:
        process = FakeProcess()
        events: list[str] = []
        job = RecordingJob(events, process)
        job.assign_error = OSError(5, "nested job denied")
        cleanup_started = asyncio.Event()
        resume = Mock()

        async def create(*args: object, **kwargs: object) -> FakeProcess:
            del args, kwargs
            return process

        async def slow_cleanup(
            actual: FakeProcess,
            process_wait: asyncio.Task[int],
            identity: object,
            actual_job: object,
        ) -> None:
            del identity, actual_job
            self.assertIs(actual, process)
            cleanup_started.set()
            await asyncio.sleep(0.03)
            process.complete(-9)
            await process_wait

        async def spawn() -> object:
            return await _windows_spawn.spawn_suspended_process(
                ("command.exe",),
                cwd=Path("."),
                environment={},
                creationflags=4,
                guard=object(),
                lease_factory=FakeLease,
                create_process=create,
                capture_identity=lambda *args: object(),
                resume_identity=resume,
                process_api=object(),
                job_factory=lambda: job,
            )

        with patch.object(
            _windows_process, "terminate_process_tree", slow_cleanup
        ):
            task = asyncio.create_task(spawn())
            await cleanup_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        resume.assert_not_called()
        self.assertEqual(process.returncode, -9)
        self.assertTrue(job.closed)

    async def test_start_cleanup_failure_wins_and_preserves_cancellation(self) -> None:
        process = FakeProcess()
        events: list[str] = []
        job = RecordingJob(events, process)
        job.assign_error = OSError(5, "nested job denied")
        cleanup_started = asyncio.Event()
        failure = ProcessTreeTerminationError(process.pid, ("cleanup failed",))

        async def create(*args: object, **kwargs: object) -> FakeProcess:
            del args, kwargs
            return process

        async def fail_cleanup(
            actual: FakeProcess,
            process_wait: asyncio.Task[int],
            identity: object,
            actual_job: object,
        ) -> None:
            del identity, actual_job
            self.assertIs(actual, process)
            cleanup_started.set()
            await asyncio.sleep(0.03)
            process.complete(-9)
            await process_wait
            raise failure

        async def spawn() -> object:
            return await _windows_spawn.spawn_suspended_process(
                ("command.exe",),
                cwd=Path("."),
                environment={},
                creationflags=4,
                guard=object(),
                lease_factory=FakeLease,
                create_process=create,
                capture_identity=lambda *args: object(),
                resume_identity=Mock(),
                process_api=object(),
                job_factory=lambda: job,
            )

        with patch.object(
            _windows_spawn, "complete_process_termination", fail_cleanup
        ):
            task = asyncio.create_task(spawn())
            await cleanup_started.wait()
            task.cancel()
            with self.assertRaises(ProcessTreeTerminationError) as raised:
                await task

        self.assertIs(raised.exception, failure)
        self.assertIsInstance(raised.exception.__cause__, asyncio.CancelledError)
        self.assertEqual(process.returncode, -9)
        self.assertTrue(job.closed)


if __name__ == "__main__":
    unittest.main()
