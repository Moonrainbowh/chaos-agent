from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from code_agent.runtime.local import WindowsLocalRuntime


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


class CompletedJob:
    def __init__(self) -> None:
        self.closed = False

    def assign(self, pid: int) -> None:
        del pid

    def terminate(self, exit_code: int = 1) -> None:
        del exit_code

    def active_processes(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class LocalRuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = WindowsLocalRuntime(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()


@contextmanager
def patch_process_identity_capture():
    with patch(
        "code_agent.runtime.local.capture_process_identity",
        return_value=object(),
    ) as capture, patch(
        "code_agent.runtime.local.resume_process_identity"
    ) as resume, patch(
        "code_agent.runtime._windows_spawn.WindowsJob.create",
        side_effect=CompletedJob,
    ):
        capture.resume = resume
        yield capture
