from __future__ import annotations

import asyncio
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
from code_agent.runtime.errors import RuntimeErrorBase, RuntimeStartError  # noqa: E402
from code_agent.runtime.local import WindowsLocalRuntime  # noqa: E402
from code_agent.runtime.models import CommandSpec, TerminationReason  # noqa: E402
from code_agent.runtime.tests._local_test_support import (  # noqa: E402
    CompletedJob,
    patch_process_identity_capture,
)


class CompletedProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class PowerShellRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = WindowsLocalRuntime(self.root)
        self.created_scripts: list[Path] = []

    def tearDown(self) -> None:
        for path in self.created_scripts:
            path.unlink(missing_ok=True)
        self.temporary.cleanup()

    def track_temporary_scripts(self):
        original = tempfile.mkstemp

        def tracked(*args: object, **kwargs: object) -> tuple[int, str]:
            descriptor, raw_path = original(*args, **kwargs)
            self.created_scripts.append(Path(raw_path))
            return descriptor, raw_path

        return patch("tempfile.mkstemp", side_effect=tracked)

    async def _assert_identity_setup_failure(self, stage: str) -> None:
        secret = f"Write-Output '{stage}-secret'"
        process = CompletedProcess()
        process.kill_calls = 0
        process.wait_calls = 0
        observed_path: Path | None = None
        leases = []
        existing_tasks = set(asyncio.all_tasks())

        original_kill = process.kill
        original_wait = process.wait

        def kill() -> None:
            process.kill_calls += 1
            original_kill()

        async def wait() -> int:
            process.wait_calls += 1
            return await original_wait()

        process.kill = kill  # type: ignore[method-assign]
        process.wait = wait  # type: ignore[method-assign]

        class TrackedLease:
            def __init__(self, path: Path, guard: object) -> None:
                del guard
                self.path = path
                self.active = False
                leases.append(self)

            def __enter__(self) -> "TrackedLease":
                self.active = True
                return self

            def __exit__(self, *exc_info: object) -> None:
                self.active = False

        async def spawn(*args: object, **kwargs: object) -> CompletedProcess:
            nonlocal observed_path
            del kwargs
            observed_path = Path(str(args[-1]))
            self.assertTrue(observed_path.exists())
            return process

        identity = object()

        def capture(*args: object) -> object:
            del args
            self.assertTrue(leases[-1].active)
            if stage == "capture":
                raise psutil.NoSuchProcess(process.pid)
            return identity

        def resume(captured: object, process_api: object) -> None:
            del process_api
            self.assertIs(captured, identity)
            self.assertTrue(leases[-1].active)
            raise psutil.AccessDenied(process.pid)

        async def terminate(
            actual_process: object,
            process_wait: asyncio.Task[int],
            actual_identity: object,
            actual_job: object,
        ) -> None:
            self.assertIs(actual_process, process)
            self.assertIs(actual_identity, identity)
            self.assertIsInstance(actual_job, CompletedJob)
            process.kill()
            await process_wait

        terminate_mock = AsyncMock(side_effect=terminate)

        with self.track_temporary_scripts(), patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            return_value="C:\\pwsh.exe",
        ), patch(
            "code_agent.runtime._powershell_runtime._probe_powershell",
            return_value=("Core", "7.6.5"),
        ), patch(
            "code_agent.runtime.local.DirectoryLease", TrackedLease
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ), patch(
            "code_agent.runtime.local.capture_process_identity", side_effect=capture
        ), patch(
            "code_agent.runtime.local.resume_process_identity", side_effect=resume
        ), patch(
            "code_agent.runtime._windows_spawn.complete_process_termination",
            terminate_mock,
        ), patch(
            "code_agent.runtime._windows_spawn.WindowsJob.create",
            side_effect=CompletedJob,
        ):
            with self.assertRaises(RuntimeErrorBase) as raised:
                await self.runtime.run(
                    CommandSpec(cwd=".", powershell_script=secret),
                    CancellationToken(),
                    None,
                )

        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(process.kill_calls, 1)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertEqual(len(leases), 1)
        self.assertFalse(leases[0].active)
        self.assertIsNotNone(observed_path)
        self.assertEqual(len(self.created_scripts), 2)
        self.assertTrue(all(not path.exists() for path in self.created_scripts))
        await asyncio.sleep(0)
        leaked_tasks = set(asyncio.all_tasks()) - existing_tasks
        self.assertFalse(leaked_tasks)
        if stage == "resume":
            terminate_mock.assert_awaited_once()
        else:
            terminate_mock.assert_not_awaited()

    async def test_capture_failure_cleans_suspended_process_and_script(self) -> None:
        await self._assert_identity_setup_failure("capture")

    async def test_resume_failure_cleans_suspended_process_and_script(self) -> None:
        await self._assert_identity_setup_failure("resume")

    async def test_secret_script_uses_file_without_argv_or_display_leak(self) -> None:
        secret = "Write-Output 'api-secret-123'"
        process = CompletedProcess()
        observed_path: Path | None = None

        async def spawn(*args: object, **kwargs: object) -> CompletedProcess:
            nonlocal observed_path
            del kwargs
            observed_path = Path(str(args[-1]))
            self.assertTrue(observed_path.exists())
            self.assertEqual(len(self.created_scripts), 2)
            payload_path, wrapper_path = self.created_scripts
            self.assertEqual(observed_path, wrapper_path)
            self.assertEqual(payload_path.read_text(encoding="utf-8-sig"), secret)
            rendered = wrapper_path.read_text(encoding="utf-8-sig")
            self.assertNotIn(secret, rendered)
            self.assertIn("ActionPreference]::Stop", rendered)
            self.assertNotIn("2>&1", rendered)
            self.assertIn("exit $__ChaosAgent_NativeExitCode", rendered)
            self.assertNotIn(secret, repr(args))
            return process

        with self.track_temporary_scripts(), patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            return_value="C:\\pwsh.exe",
        ), patch(
            "code_agent.runtime._powershell_runtime._probe_powershell",
            return_value=("Core", "7.6.5"),
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ), patch_process_identity_capture():
            result = await self.runtime.run(
                CommandSpec(cwd=".", powershell_script=secret),
                CancellationToken(),
                None,
            )

        self.assertIsNotNone(observed_path)
        self.assertEqual(len(self.created_scripts), 2)
        self.assertTrue(all(not path.exists() for path in self.created_scripts))
        self.assertEqual(result.display_command, "<powershell-script>")
        self.assertNotIn(secret, result.argv)
        self.assertNotIn(secret, result.display_command)

    async def test_start_error_does_not_leak_script_and_removes_temp(self) -> None:
        secret = "Write-Output 'start-secret-456'"
        observed_path: Path | None = None

        async def fail_start(*args: object, **kwargs: object) -> None:
            nonlocal observed_path
            del kwargs
            observed_path = Path(str(args[-1]))
            raise OSError("spawn unavailable")

        with self.track_temporary_scripts(), patch(
            "code_agent.runtime._powershell_runtime.shutil.which",
            return_value="C:\\pwsh.exe",
        ), patch(
            "code_agent.runtime._powershell_runtime._probe_powershell",
            return_value=("Core", "7.6.5"),
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec",
            side_effect=fail_start,
        ):
            with self.assertRaises(RuntimeStartError) as raised:
                await self.runtime.run(
                    CommandSpec(cwd=".", powershell_script=secret),
                    CancellationToken(),
                    None,
                )

        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNotNone(observed_path)
        self.assertEqual(len(self.created_scripts), 2)
        self.assertTrue(all(not path.exists() for path in self.created_scripts))

    async def test_unicode_script_runs_from_utf8_bom_file(self) -> None:
        script = "Write-Output '\u4f60\u597d\U0001f642'"

        result = await self.runtime.run(
            CommandSpec(cwd=".", powershell_script=script),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 0)
        self.assertIn("\u4f60\u597d\U0001f642".encode("utf-8"), result.stdout)

    async def test_cancelled_script_removes_temp_file(self) -> None:
        token = CancellationToken()
        asyncio.get_running_loop().call_later(0.1, token.cancel, "user stop")

        with self.track_temporary_scripts():
            result = await self.runtime.run(
                CommandSpec(
                    cwd=".", powershell_script="Start-Sleep -Seconds 5"
                ),
                token,
                None,
            )

        self.assertEqual(result.reason, TerminationReason.CANCELLED)
        self.assertEqual(len(self.created_scripts), 2)
        self.assertTrue(all(not path.exists() for path in self.created_scripts))


if __name__ == "__main__":
    unittest.main()
