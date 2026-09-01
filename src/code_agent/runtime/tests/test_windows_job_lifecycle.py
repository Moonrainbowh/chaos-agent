from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _windows_job, _windows_process, _windows_spawn  # noqa: E402
from code_agent.runtime.errors import (  # noqa: E402
    ProcessTreeTerminationError,
    RuntimeStartError,
)
from code_agent.runtime.tests._windows_job_support import (  # noqa: E402
    FakeJobApi,
    FakeLease,
    FakeProcess,
    RecordingJob,
)


class WindowsJobHandleOwnershipTests(unittest.TestCase):
    def test_assignment_close_failure_retains_owned_handle_for_retry(self) -> None:
        class CloseOnceApi(FakeJobApi):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.failed_once = False

            def close_handle(inner_self, handle: int) -> None:
                inner_self.events.append(("close_handle", handle))
                if handle == 200 and not inner_self.failed_once:
                    inner_self.failed_once = True
                    raise OSError(5, "temporary process handle close failed")

        api = CloseOnceApi()
        job = _windows_job.WindowsJob.create(api)

        with self.assertRaisesRegex(OSError, "temporary process handle"):
            job.assign(4321)

        self.assertTrue(job.assigned)
        self.assertFalse(job.closed)
        job.close()
        self.assertTrue(job.closed)
        self.assertEqual(api.events.count(("close_handle", 200)), 2)
        self.assertIn(("close_handle", 100), api.events)


class WindowsJobLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_assign_close_failure_uses_assigned_job_cleanup(self) -> None:
        process = FakeProcess()
        events: list[str] = []

        class AssignedThenFailedJob(RecordingJob):
            def __init__(inner_self) -> None:
                super().__init__(events, process)
                inner_self._assigned = False

            @property
            def assigned(inner_self) -> bool:
                return inner_self._assigned

            def assign(inner_self, pid: int) -> None:
                events.append(f"assign:{pid}")
                inner_self._assigned = True
                raise OSError(5, "process handle close failed")

        async def create(*args: object, **kwargs: object) -> FakeProcess:
            del args, kwargs
            return process

        job = AssignedThenFailedJob()
        resume = Mock()
        with self.assertRaises(RuntimeStartError):
            await _windows_spawn.spawn_suspended_process(
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

        resume.assert_not_called()
        self.assertEqual(job.terminate_calls, 1)
        self.assertTrue(job.closed)

    async def test_termination_waits_until_job_accounting_is_empty(self) -> None:
        process = FakeProcess()
        process_wait = asyncio.create_task(process.wait())
        events: list[str] = []

        class PollingJob(RecordingJob):
            def __init__(inner_self) -> None:
                super().__init__(events, process)
                inner_self.counts = iter((2, 1, 0))

            def active_processes(inner_self) -> int:
                value = next(inner_self.counts)
                events.append(f"active:{value}")
                return value

        job = PollingJob()
        try:
            await _windows_process.terminate_process_tree(
                process, process_wait, object(), job
            )
        finally:
            if not process_wait.done():
                process_wait.cancel()
            await asyncio.gather(process_wait, return_exceptions=True)

        self.assertEqual(events[:4], ["terminate:1", "active:2", "active:1", "active:0"])

    async def test_normal_root_exit_terminates_remaining_job_members(self) -> None:
        process = FakeProcess()
        process.complete(0)
        process_wait = asyncio.create_task(process.wait())
        events: list[str] = []

        class DescendantJob(RecordingJob):
            def __init__(inner_self) -> None:
                super().__init__(events, process)
                inner_self.counts = iter((1, 0))

            def active_processes(inner_self) -> int:
                return next(inner_self.counts)

        job = DescendantJob()
        await _windows_process.terminate_process_tree(
            process, process_wait, object(), job
        )
        await process_wait

        self.assertEqual(job.terminate_calls, 1)

    async def test_unknown_membership_still_attempts_job_termination(self) -> None:
        process = FakeProcess()
        process.complete(0)
        process_wait = asyncio.create_task(process.wait())

        class FailedQueryJob:
            terminate_calls = 0

            def active_processes(inner_self) -> int:
                raise OSError(5, "Job accounting denied")

            def terminate(inner_self) -> None:
                inner_self.terminate_calls += 1

        job = FailedQueryJob()
        with self.assertRaises(ProcessTreeTerminationError):
            await _windows_process.terminate_process_tree(
                process, process_wait, object(), job
            )
        await process_wait

        self.assertEqual(job.terminate_calls, 1)

    async def test_external_cancellation_waits_for_job_cleanup(self) -> None:
        process = FakeProcess()
        process_wait = asyncio.create_task(process.wait())

        class SlowJob:
            def __init__(inner_self) -> None:
                inner_self.terminate_calls = 0
                inner_self.active_calls = 0

            def terminate(inner_self) -> None:
                inner_self.terminate_calls += 1

            def active_processes(inner_self) -> int:
                inner_self.active_calls += 1
                if inner_self.active_calls >= 3:
                    process.complete(1)
                    return 0
                return 1

        job = SlowJob()
        with patch.object(_windows_process, "_JOB_POLL_INTERVAL_S", 0.02):
            cleanup = asyncio.create_task(
                _windows_process.complete_process_termination(
                    process, process_wait, object(), job
                )
            )
            await asyncio.sleep(0.005)
            cleanup.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cleanup
        await process_wait

        self.assertEqual(job.terminate_calls, 1)
        self.assertGreaterEqual(job.active_calls, 3)

    async def test_pending_output_reader_is_not_silent_success(self) -> None:
        reader = asyncio.create_task(asyncio.Event().wait())
        try:
            with patch.object(_windows_process, "_TASK_CLEANUP_TIMEOUT_S", 0.01):
                with self.assertRaises(ProcessTreeTerminationError) as raised:
                    await _windows_process.require_output_tasks((reader,), 8123)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

        self.assertIn("did not reach EOF", str(raised.exception))

    async def test_close_failure_preserves_prior_runtime_failure_fact(self) -> None:
        class FailedCloseJob:
            closed = False

            def close(inner_self) -> None:
                raise OSError(5, "CloseHandle denied")

        async def fail_then_finish() -> None:
            try:
                raise ValueError("callback failed")
            finally:
                await _windows_process.finish_process_tasks(
                    FailedCloseJob(), 8123, ()
                )

        with self.assertRaises(ProcessTreeTerminationError) as raised:
            await fail_then_finish()

        self.assertTrue(
            any("prior runtime failure: ValueError" in item for item in raised.exception.failures)
        )
        self.assertTrue(
            any("CloseHandle(job)" in item for item in raised.exception.failures)
        )


if __name__ == "__main__":
    unittest.main()
