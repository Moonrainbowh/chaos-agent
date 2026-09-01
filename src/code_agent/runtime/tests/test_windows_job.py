from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psutil


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.runtime import _windows_job, _windows_process, _windows_spawn  # noqa: E402
from code_agent.runtime.errors import (  # noqa: E402
    ProcessTreeTerminationError,
    RuntimeStartError,
)
from code_agent.runtime.local import WindowsLocalRuntime  # noqa: E402
from code_agent.runtime.models import CommandSpec, TerminationReason  # noqa: E402
from code_agent.runtime.tests._windows_job_support import (  # noqa: E402
    FakeJobApi,
    FakeLease,
    FakeProcess,
    RecordingJob,
)


class WindowsJobContractTests(unittest.TestCase):
    def test_create_configures_kill_on_close_and_close_is_idempotent(self) -> None:
        api = FakeJobApi()

        job = _windows_job.WindowsJob.create(api)
        self.assertFalse(job.closed)
        job.close()
        job.close()

        self.assertTrue(job.closed)
        self.assertEqual(
            api.events,
            [("create_job",), ("set_kill_on_close", 100), ("close_handle", 100)],
        )

    def test_configuration_failure_closes_new_job_handle(self) -> None:
        api = FakeJobApi("configure")

        with self.assertRaises(OSError):
            _windows_job.WindowsJob.create(api)

        self.assertEqual(api.events[-1], ("close_handle", 100))

    def test_nested_job_assignment_failure_closes_only_process_handle(self) -> None:
        api = FakeJobApi("assign")
        job = _windows_job.WindowsJob.create(api)

        with self.assertRaisesRegex(OSError, "already in"):
            job.assign(4321)

        self.assertEqual(api.events[-1], ("close_handle", 200))
        self.assertFalse(job.closed)
        job.close()

    def test_assigned_job_delegates_termination_and_accounting(self) -> None:
        api = FakeJobApi()
        job = _windows_job.WindowsJob.create(api)

        job.assign(4321)
        job.terminate(7)
        self.assertEqual(job.active_processes(), 0)

        self.assertIn(("terminate_job", 100, 7), api.events)
        self.assertIn(("active_processes", 100), api.events)
        job.close()


class WindowsJobSpawnTests(unittest.IsolatedAsyncioTestCase):
    async def _spawn(
        self,
        process: FakeProcess,
        job: RecordingJob,
        events: list[str],
        resume: object,
    ) -> tuple[object, object, object]:
        async def create_process(*args: object, **kwargs: object) -> FakeProcess:
            del args, kwargs
            events.append("create_process_suspended")
            return process

        def factory() -> RecordingJob:
            events.append("create_configured_job")
            return job

        def capture(*args: object) -> object:
            del args
            events.append("capture")
            return object()

        return await _windows_spawn.spawn_suspended_process(
            ("command.exe",),
            cwd=Path("."),
            environment={},
            creationflags=4,
            guard=object(),
            lease_factory=FakeLease,
            create_process=create_process,
            capture_identity=capture,
            resume_identity=resume,  # type: ignore[arg-type]
            process_api=object(),
            job_factory=factory,
        )

    async def test_assigns_suspended_process_before_resume(self) -> None:
        events: list[str] = []
        process = FakeProcess()
        job = RecordingJob(events)

        def resume(*args: object) -> None:
            del args
            events.append("resume")

        actual_process, _, actual_job = await self._spawn(
            process, job, events, resume
        )

        self.assertIs(actual_process, process)
        self.assertIs(actual_job, job)
        self.assertEqual(
            events,
            [
                "create_configured_job",
                "create_process_suspended",
                "capture",
                f"assign:{process.pid}",
                "resume",
            ],
        )

    async def test_assign_failure_never_resumes_and_cleans_up(self) -> None:
        events: list[str] = []
        process = FakeProcess()
        job = RecordingJob(events, process)
        job.assign_error = OSError(5, "nested job denied")
        resume = unittest.mock.Mock()

        async def terminate(*args: object) -> None:
            del args
            events.append("fallback_tree_cleanup")
            process.complete(-9)

        with patch.object(
            _windows_spawn,
            "complete_process_termination",
            AsyncMock(side_effect=terminate),
        ):
            with self.assertRaises(RuntimeStartError):
                await self._spawn(process, job, events, resume)

        resume.assert_not_called()
        self.assertTrue(job.closed)
        self.assertIn("fallback_tree_cleanup", events)

    async def test_job_termination_failure_still_falls_back_and_is_reported(self) -> None:
        events: list[str] = []
        process = FakeProcess()
        process_wait = asyncio.create_task(process.wait())

        class FailedJob:
            def terminate(inner_self) -> None:
                events.append("terminate_job")
                raise OSError(5, "TerminateJobObject denied")

        async def bounded(function: object, *, timeout: float) -> object:
            del function, timeout
            events.append("fallback_tree_discovery")
            raise psutil.NoSuchProcess(process.pid)

        try:
            with patch.object(_windows_process, "_bounded_to_thread", bounded):
                with self.assertRaises(ProcessTreeTerminationError) as raised:
                    await _windows_process.terminate_process_tree(
                        process, process_wait, object(), FailedJob()
                    )
        finally:
            if not process_wait.done():
                process_wait.cancel()
            await asyncio.gather(process_wait, return_exceptions=True)

        self.assertEqual(events[:2], ["terminate_job", "fallback_tree_discovery"])
        self.assertTrue(any("TerminateJobObject" in item for item in raised.exception.failures))


class WindowsJobRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = WindowsLocalRuntime(self.root)

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def _run_reason(self, reason: TerminationReason) -> RecordingJob:
        chunks = (b"too much",) if reason is TerminationReason.OUTPUT_LIMIT else ()
        process = FakeProcess(chunks)
        events: list[str] = []
        job = RecordingJob(events, process)
        token = CancellationToken()
        if reason is TerminationReason.CANCELLED:
            asyncio.get_running_loop().call_later(0.01, token.cancel, "stop")

        async def spawn(*args: object, **kwargs: object) -> tuple[object, ...]:
            del args, kwargs
            return process, object(), job

        async def terminate(
            actual: FakeProcess, wait: object, identity: object, actual_job: object
        ) -> None:
            del wait, identity
            self.assertIs(actual, process)
            self.assertIs(actual_job, job)
            job.terminate()

        spec = CommandSpec(
            cwd=".",
            argv=(sys.executable, "-c", "pass"),
            timeout_s=0.01 if reason is TerminationReason.TIMEOUT else 5,
            max_output_bytes=1 if reason is TerminationReason.OUTPUT_LIMIT else 1024,
        )
        with patch("code_agent.runtime.local.spawn_suspended_process", spawn), patch(
            "code_agent.runtime.local.complete_process_termination",
            side_effect=terminate,
        ):
            result = await self.runtime.run(spec, token, None)

        self.assertEqual(result.reason, reason)
        self.assertEqual(job.terminate_calls, 1)
        self.assertTrue(job.closed)
        return job

    async def test_timeout_cancel_and_output_limit_terminate_job(self) -> None:
        for reason in (
            TerminationReason.TIMEOUT,
            TerminationReason.CANCELLED,
            TerminationReason.OUTPUT_LIMIT,
        ):
            with self.subTest(reason=reason):
                await self._run_reason(reason)

    async def test_normal_exit_closes_job_without_terminating_it(self) -> None:
        process = FakeProcess()
        process.complete(0)
        job = RecordingJob([], process)

        async def spawn(*args: object, **kwargs: object) -> tuple[object, ...]:
            del args, kwargs
            return process, object(), job

        with patch("code_agent.runtime.local.spawn_suspended_process", spawn):
            result = await self.runtime.run(
                CommandSpec(cwd=".", argv=(sys.executable, "-V")),
                CancellationToken(),
                None,
            )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(job.terminate_calls, 0)
        self.assertTrue(job.closed)

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object smoke test")
    async def test_real_normal_parent_exit_kills_inherited_child(self) -> None:
        marker = self.root / "job-child-survived.txt"
        child_code = (
            "import pathlib,time;time.sleep(0.8);"
            f"pathlib.Path({str(marker)!r}).write_text('alive',encoding='utf-8')"
        )
        parent_code = (
            "import subprocess,sys;"
            "subprocess.Popen([sys.executable,'-c',"
            f"{child_code!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        )

        result = await self.runtime.run(
            CommandSpec(cwd=".", argv=(sys.executable, "-c", parent_code)),
            CancellationToken(),
            None,
        )
        await asyncio.sleep(1.0)

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertFalse(marker.exists(), "KILL_ON_JOB_CLOSE did not kill child")


if __name__ == "__main__":
    unittest.main()
