from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _windows_spawn  # noqa: E402
from code_agent.runtime.errors import (  # noqa: E402
    ProcessTreeTerminationError,
    RuntimeStartError,
)
from code_agent.runtime.tests._windows_job_support import RecordingJob  # noqa: E402


class FakeReader:
    def __init__(self, mode: str = "eof") -> None:
        self.mode = mode

    async def read(self) -> bytes:
        if self.mode == "hang":
            await asyncio.Event().wait()
        if self.mode == "error":
            raise ValueError("pipe exploded")
        return b""


class FakeProcess:
    def __init__(
        self,
        *,
        kill_error: BaseException | None = None,
        complete_on_kill: bool = True,
        wait_initially_done: bool = False,
        stdout_mode: str = "eof",
        stderr_mode: str = "eof",
    ) -> None:
        self.pid = 8100
        self.returncode: int | None = None
        self.stdout = FakeReader(stdout_mode)
        self.stderr = FakeReader(stderr_mode)
        self.kill_error = kill_error
        self.complete_on_kill = complete_on_kill
        self.kill_calls = 0
        self.wait_calls = 0
        self._done = asyncio.Event()
        if wait_initially_done:
            self._done.set()

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error
        if self.complete_on_kill:
            self.complete(-9)

    def complete(self, returncode: int = -9) -> None:
        self.returncode = returncode
        self._done.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._done.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeLease:
    def __init__(self, path: Path, guard: object) -> None:
        del guard
        self.path = path

    def __enter__(self) -> "FakeLease":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class WindowsSpawnFailureTests(unittest.IsolatedAsyncioTestCase):
    async def _spawn(
        self,
        process: FakeProcess,
        *,
        capture_identity: object,
        resume_identity: object,
    ) -> tuple[object, object, object]:
        async def create_process(*args: object, **kwargs: object) -> FakeProcess:
            del args, kwargs
            return process

        return await _windows_spawn.spawn_suspended_process(
            ("command.exe",),
            cwd=Path("."),
            environment={},
            creationflags=4,
            guard=object(),
            lease_factory=FakeLease,
            create_process=create_process,
            capture_identity=capture_identity,  # type: ignore[arg-type]
            resume_identity=resume_identity,  # type: ignore[arg-type]
            process_api=object(),
            job_factory=lambda: RecordingJob([], process),
        )

    async def _assert_capture_cleanup_failure(
        self,
        process: FakeProcess,
        expected_failure: str,
    ) -> ProcessTreeTerminationError:
        setup_error = ValueError("capture failed")

        def capture(*args: object) -> object:
            del args
            raise setup_error

        with patch.object(_windows_spawn, "_STARTUP_CLEANUP_TIMEOUT_S", 0.01):
            with self.assertRaises(ProcessTreeTerminationError) as raised:
                await self._spawn(
                    process,
                    capture_identity=capture,
                    resume_identity=lambda *args: None,
                )

        self.assertIs(raised.exception.__cause__, setup_error)
        self.assertTrue(
            any(expected_failure in failure for failure in raised.exception.failures)
        )
        return raised.exception

    async def test_root_kill_error_is_not_reported_as_successful_cleanup(self) -> None:
        process = FakeProcess(
            kill_error=OSError("kill denied"),
            wait_initially_done=True,
        )

        await self._assert_capture_cleanup_failure(process, "root kill")

        self.assertEqual(process.kill_calls, 1)
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_root_wait_timeout_is_not_reported_as_successful_cleanup(self) -> None:
        process = FakeProcess(complete_on_kill=False)

        await self._assert_capture_cleanup_failure(process, "root wait")

        self.assertEqual(process.kill_calls, 1)

    async def test_root_wait_without_returncode_is_not_successful_cleanup(self) -> None:
        class MissingReturncodeProcess(FakeProcess):
            def kill(inner_self) -> None:
                inner_self.kill_calls += 1
                inner_self._done.set()

            async def wait(inner_self) -> int:
                inner_self.wait_calls += 1
                await inner_self._done.wait()
                return -9

        process = MissingReturncodeProcess()

        await self._assert_capture_cleanup_failure(process, "root wait")

        self.assertIsNone(process.returncode)

    async def test_pipe_drain_timeout_is_reported(self) -> None:
        process = FakeProcess(stdout_mode="hang")

        await self._assert_capture_cleanup_failure(process, "stdout pipe cleanup")

    async def test_pipe_drain_exception_is_reported(self) -> None:
        process = FakeProcess(stderr_mode="error")

        await self._assert_capture_cleanup_failure(process, "stderr pipe cleanup")

    async def test_resume_failure_uses_same_identity_for_tree_cleanup(self) -> None:
        process = FakeProcess(complete_on_kill=False)
        identity = object()
        setup_error = ValueError("resume partially failed")

        def resume(*args: object) -> None:
            del args
            raise setup_error

        async def terminate(
            actual_process: object,
            process_wait: asyncio.Task[int],
            actual_identity: object,
            actual_job: object,
        ) -> None:
            self.assertIs(actual_process, process)
            self.assertIs(actual_identity, identity)
            self.assertIsInstance(actual_job, RecordingJob)
            process.complete()
            await process_wait

        terminate_mock = AsyncMock(side_effect=terminate)
        with patch.object(
            _windows_spawn, "complete_process_termination", terminate_mock
        ):
            with self.assertRaises(RuntimeStartError) as raised:
                await self._spawn(
                    process,
                    capture_identity=lambda *args: identity,
                    resume_identity=resume,
                )

        self.assertIs(raised.exception.__cause__, setup_error)
        terminate_mock.assert_awaited_once()
        self.assertEqual(process.kill_calls, 0)

    async def test_tree_cleanup_failure_has_priority_over_resume_error(self) -> None:
        process = FakeProcess(wait_initially_done=True)
        identity = object()
        setup_error = ValueError("resume failed")
        tree_error = ProcessTreeTerminationError(
            process.pid, ("tree cleanup failed",)
        )

        terminate_mock = AsyncMock(side_effect=tree_error)
        with patch.object(
            _windows_spawn, "complete_process_termination", terminate_mock
        ):
            with self.assertRaises(ProcessTreeTerminationError) as raised:
                await self._spawn(
                    process,
                    capture_identity=lambda *args: identity,
                    resume_identity=lambda *args: (_ for _ in ()).throw(setup_error),
                )

        self.assertIs(raised.exception, tree_error)
        self.assertIs(raised.exception.__cause__, setup_error)
        self.assertEqual(raised.exception.failures, ("tree cleanup failed",))


if __name__ == "__main__":
    unittest.main()
