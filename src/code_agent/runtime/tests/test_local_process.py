from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil
import subprocess
import tempfile


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.runtime.errors import RuntimeStartError  # noqa: E402
from code_agent.runtime import _process_snapshot  # noqa: E402
from code_agent.runtime.models import CommandSpec, TerminationReason  # noqa: E402
from code_agent.runtime.tests._local_test_support import (  # noqa: E402
    CompletedProcess,
    LocalRuntimeTestCase,
    patch_process_identity_capture,
)


class WindowsLocalProcessTests(LocalRuntimeTestCase):
    async def test_create_suspended_blocks_marker_until_identity_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "suspended-marker.txt"
            code = (
                "import pathlib;"
                f"pathlib.Path({str(marker)!r}).write_text('resumed',encoding='utf-8');"
                "print('resumed',flush=True)"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | 0x00000004
                ),
            )
            try:
                await asyncio.sleep(0.2)
                self.assertFalse(marker.exists())
                identity = _process_snapshot.capture_process_identity(
                    process.pid, psutil
                )
                _process_snapshot.resume_process_identity(identity, psutil)
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=2.0
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=1.0)

            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout.strip(), b"resumed")
            self.assertEqual(stderr, b"")
            self.assertTrue(marker.exists())

    async def test_runtime_resumes_before_command_writes_marker(self) -> None:
        marker = self.root / "runtime-resume-marker.txt"
        code = (
            "import pathlib;"
            f"pathlib.Path({str(marker)!r}).write_text('ready',encoding='utf-8');"
            "print('ready',flush=True)"
        )

        result = await self.runtime.run(
            CommandSpec(cwd=".", argv=(sys.executable, "-c", code)),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.stdout.strip(), b"ready")
        self.assertEqual(marker.read_text(encoding="utf-8"), "ready")

    async def test_timeout_returns_structured_reason(self) -> None:
        result = await self.runtime.run(
            CommandSpec(
                cwd=".",
                argv=(sys.executable, "-c", "import time;time.sleep(5)"),
                timeout_s=0.1,
            ),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.TIMEOUT)
        self.assertIsNone(result.returncode)
        self.assertIsNone(result.cancellation_reason)
        self.assertLess(result.duration_s, 3)

    async def test_cancellation_returns_structured_reason(self) -> None:
        token = CancellationToken()
        (self.root / "nested").mkdir()
        asyncio.get_running_loop().call_later(0.1, token.cancel, "user stop")

        result = await self.runtime.run(
            CommandSpec(
                cwd="nested",
                argv=(sys.executable, "-c", "import time;time.sleep(5)"),
            ),
            token,
            None,
        )

        self.assertEqual(result.reason, TerminationReason.CANCELLED)
        self.assertIsNone(result.returncode)
        self.assertEqual(result.cwd, "nested")
        self.assertEqual(result.cancellation_reason, "user stop")
        self.assertLess(result.duration_s, 3)

    async def test_timeout_terminates_descendant_before_delayed_marker(self) -> None:
        marker = self.root / "descendant-marker.txt"
        identity_file = self.root / "descendant-identity.txt"
        child_code = (
            "import time,pathlib;time.sleep(1.2);"
            f"pathlib.Path({str(marker)!r}).write_text('alive',encoding='utf-8')"
        )
        parent_code = (
            "import pathlib,psutil,subprocess,sys,time;"
            f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
            f"pathlib.Path({str(identity_file)!r}).write_text("
            "f'{child.pid}|{psutil.Process(child.pid).create_time()}',encoding='utf-8');"
            "print('spawned',flush=True);time.sleep(5)"
        )

        result = await self.runtime.run(
            CommandSpec(
                cwd=".",
                argv=(sys.executable, "-c", parent_code),
                timeout_s=0.3,
            ),
            CancellationToken(),
            None,
        )
        child_pid_text, child_created_text = identity_file.read_text(
            encoding="utf-8"
        ).split("|", maxsplit=1)
        child_pid = int(child_pid_text)
        child_created = float(child_created_text)
        await asyncio.sleep(1.3)

        self.assertEqual(result.reason, TerminationReason.TIMEOUT)
        self.assertIn(b"spawned", result.stdout)
        self.assertFalse(marker.exists(), "descendant survived process-tree termination")
        try:
            current = psutil.Process(child_pid)
            same_identity = abs(current.create_time() - child_created) <= 1e-6
            original_is_running = (
                same_identity
                and current.is_running()
                and current.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            original_is_running = False
        self.assertFalse(
            original_is_running,
            f"child identity {child_pid}@{child_created} survived termination",
        )

    async def test_directory_lease_is_held_only_while_spawn_is_awaited(self) -> None:
        leases = []

        class LeaseAwareProcess(CompletedProcess):
            async def wait(inner_self) -> int:
                self.assertFalse(leases[-1].active)
                return await super().wait()

        process = LeaseAwareProcess()

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
            del args
            self.assertTrue(leases[-1].active)
            self.assertEqual(kwargs["cwd"], str(leases[-1].path))
            return process

        identity = object()

        def capture(*args: object) -> object:
            del args
            self.assertTrue(leases[-1].active)
            return identity

        def resume(captured: object, process_api: object) -> None:
            del process_api
            self.assertIs(captured, identity)
            self.assertTrue(leases[-1].active)

        with patch(
            "code_agent.runtime.local.DirectoryLease", TrackedLease
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ), patch(
            "code_agent.runtime.local.capture_process_identity", side_effect=capture
        ), patch(
            "code_agent.runtime.local.resume_process_identity", side_effect=resume
        ):
            result = await self.runtime.run(
                CommandSpec(cwd=".", argv=(sys.executable, "-V")),
                CancellationToken(),
                None,
            )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(len(leases), 1)
        self.assertFalse(leases[0].active)

    async def test_directory_lease_is_released_when_spawn_fails(self) -> None:
        leases = []

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

        async def fail_start(*args: object, **kwargs: object) -> CompletedProcess:
            del args, kwargs
            self.assertTrue(leases[-1].active)
            raise OSError("cannot start")

        with patch(
            "code_agent.runtime.local.DirectoryLease", TrackedLease
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec",
            side_effect=fail_start,
        ):
            with self.assertRaises(RuntimeStartError):
                await self.runtime.run(
                    CommandSpec(cwd=".", argv=(sys.executable, "-V")),
                    CancellationToken(),
                    None,
                )

        self.assertEqual(len(leases), 1)
        self.assertFalse(leases[0].active)


if __name__ == "__main__":
    unittest.main()
