from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.git import (  # noqa: E402
    GitCommandError,
    GitTimeoutError,
    GitWorkspace,
)


class ControlledProcess:
    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None
        self.killed = False
        self.wait_calls = 0
        self.wait_timeouts: list[float | None] = []

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        if timeout is None:
            raise AssertionError("wait must always be bounded")
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class HangingProcess(ControlledProcess):
    def __init__(self, stdout: bytes = b"") -> None:
        super().__init__(stdout, b"")
        self._finished = threading.Event()

    def kill(self) -> None:
        super().kill()
        self._finished.set()

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        if not self._finished.wait(timeout):
            raise subprocess.TimeoutExpired("git", timeout)
        assert self.returncode is not None
        return self.returncode


class StubbornProcess(ControlledProcess):
    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        if timeout is None:
            raise AssertionError("wait must always be bounded")
        raise subprocess.TimeoutExpired("git", timeout)


class ControlledThread:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.joined = False
        self.join_timeouts: list[float] = []

    def start(self) -> None:
        if self.fail_start:
            raise OSError("thread unavailable")

    def join(self, timeout: float | None = None) -> None:
        if timeout is None:
            raise AssertionError("join must always be bounded")
        self.join_timeouts.append(timeout)
        self.joined = True


class GitOutputLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_output_limit_raises_structured_error(self) -> None:
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True, shell=False
        )
        (self.root / "a-very-long-untracked-filename.txt").write_text(
            "content", encoding="utf-8"
        )

        with self.assertRaises(GitCommandError) as raised:
            GitWorkspace(self.root, max_output_bytes=8).status_porcelain()

        self.assertEqual(raised.exception.operation, "status")
        self.assertIn("output limit", str(raised.exception))

    def test_combined_pipe_limit_kills_process_and_bounds_captured_bytes(
        self,
    ) -> None:
        process = ControlledProcess(b"12345678", b"abcdefgh")
        with patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ) as popen:
            with patch(
                "code_agent.workspace.git.subprocess.run",
                side_effect=AssertionError("subprocess.run must not buffer pipes"),
            ):
                with self.assertRaises(GitCommandError) as raised:
                    GitWorkspace(self.root, max_output_bytes=10).status_porcelain()

        error = raised.exception
        self.assertEqual(type(error).__name__, "GitOutputLimitError")
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertLessEqual(len(error.stdout_bytes) + len(error.stderr_bytes), 10)
        self.assertEqual(popen.call_args.kwargs["bufsize"], 0)

    def test_reader_start_failure_cleans_process_pipes_and_started_thread(
        self,
    ) -> None:
        process = ControlledProcess(b"", b"")
        first = ControlledThread()
        second = ControlledThread(fail_start=True)
        with patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ), patch(
            "code_agent.workspace._git_process.threading.Thread",
            side_effect=(first, second),
        ):
            with self.assertRaises(OSError):
                GitWorkspace(self.root).status_porcelain()

        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(first.joined)
        self.assertFalse(second.joined)

    def test_hanging_process_times_out_without_hanging_the_test(self) -> None:
        process = HangingProcess(b"partial output")
        errors: list[BaseException] = []
        workspace = GitWorkspace(self.root, timeout_s=0.05)

        def invoke() -> None:
            try:
                workspace.status_porcelain()
            except BaseException as error:
                errors.append(error)

        with patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ):
            worker = threading.Thread(target=invoke, daemon=True)
            worker.start()
            worker.join(timeout=0.3)
            was_still_running = worker.is_alive()
            if was_still_running:
                process.kill()
                worker.join(timeout=0.5)

        self.assertFalse(was_still_running, "Git wait exceeded its timeout")
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(type(error).__name__, "GitTimeoutError")
        self.assertEqual(error.stdout_bytes, b"partial output")  # type: ignore[attr-defined]
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(all(value is not None for value in process.wait_timeouts))

    def test_thread_constructor_failure_still_cleans_process_and_pipes(
        self,
    ) -> None:
        process = ControlledProcess(b"", b"")
        with patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ), patch(
            "code_agent.workspace._git_process.threading.Thread",
            side_effect=(ControlledThread(), OSError("thread unavailable")),
        ):
            with self.assertRaises(OSError):
                GitWorkspace(self.root).status_porcelain()

        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_timeout_cleanup_calls_zero_timeout_wait_and_every_join(self) -> None:
        process = StubbornProcess(b"", b"")
        first = ControlledThread()
        second = ControlledThread()
        with patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ), patch(
            "code_agent.workspace._git_process.threading.Thread",
            side_effect=(first, second),
        ), patch(
            "code_agent.workspace._git_process.time.monotonic",
            side_effect=(0.0, 2.0, 2.0, 2.0),
        ):
            with self.assertRaises(GitTimeoutError):
                GitWorkspace(self.root, timeout_s=0.01).status_porcelain()

        self.assertGreaterEqual(len(process.wait_timeouts), 2)
        self.assertEqual(process.wait_timeouts[-1], 0.0)
        self.assertEqual(first.join_timeouts, [0.0])
        self.assertEqual(second.join_timeouts, [0.0])


if __name__ == "__main__":
    unittest.main()
