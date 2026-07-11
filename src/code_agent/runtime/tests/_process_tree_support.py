from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from code_agent.runtime import _windows_process
from code_agent.runtime._process_snapshot import (
    ProcessIdentity,
    capture_process_identity,
)


class FakeNoSuchProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakeProcess:
    def __init__(
        self,
        name: str,
        pid: int,
        created: float,
        events: list[tuple[object, ...]],
        *,
        children_rounds: list[list["FakeProcess"]] | None = None,
        suspend_error: BaseException | None = None,
        identity_after_root_kill: float | BaseException | None = None,
        create_time_error: BaseException | None = None,
        resume_error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.pid = pid
        self._created = created
        self._events = events
        self._children_rounds = list(children_rounds or [[]])
        self._suspend_error = suspend_error
        self._identity_after_root_kill = identity_after_root_kill
        self._create_time_error = create_time_error
        self._resume_error = resume_error

    def create_time(self) -> float:
        self._events.append((self.name, "create_time"))
        if self._create_time_error is not None:
            raise self._create_time_error
        changed = self._identity_after_root_kill
        if changed is not None and ("root_handle", "kill") in self._events:
            if isinstance(changed, BaseException):
                raise changed
            return changed
        return self._created

    def suspend(self) -> None:
        self._events.append((self.name, "suspend"))
        if self._suspend_error is not None:
            raise self._suspend_error

    def children(self, *, recursive: bool) -> list["FakeProcess"]:
        self._events.append((self.name, "children", recursive))
        if len(self._children_rounds) > 1:
            return self._children_rounds.pop(0)
        return self._children_rounds[0]

    def kill(self) -> None:
        self._events.append((self.name, "kill"))

    def resume(self) -> None:
        self._events.append((self.name, "resume"))
        if self._resume_error is not None:
            raise self._resume_error


class FakePsutil:
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeAccessDenied

    def __init__(
        self,
        root: FakeProcess | BaseException,
        events: list[tuple[object, ...]],
        wait_results: list[tuple[list[FakeProcess], list[FakeProcess]]] | None = None,
    ) -> None:
        self._root = root
        self._events = events
        self._wait_results = list(wait_results or [])

    def Process(self, pid: int) -> FakeProcess:
        self._events.append(("psutil", "Process", pid))
        if isinstance(self._root, BaseException):
            raise self._root
        return self._root

    def wait_procs(
        self, processes: list[FakeProcess], timeout: float
    ) -> tuple[list[FakeProcess], list[FakeProcess]]:
        self._events.append(
            ("psutil", "wait_procs", tuple(item.name for item in processes), timeout)
        )
        if self._wait_results:
            return self._wait_results.pop(0)
        return list(processes), []


class FakeAsyncProcess:
    def __init__(
        self,
        events: list[tuple[object, ...]],
        *,
        complete_after_kill: bool = True,
    ) -> None:
        self.pid = 7000
        self.returncode: int | None = None
        self._events = events
        self._complete_after_kill = complete_after_kill
        self._exited = asyncio.Event()

    def kill(self) -> None:
        self._events.append(("root_handle", "kill"))
        if self._complete_after_kill:
            self.returncode = -9
            self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


class ProcessTreeTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.process = FakeAsyncProcess(self.events)
        self.process_wait = asyncio.create_task(self.process.wait())

    async def asyncTearDown(self) -> None:
        if not self.process_wait.done():
            self.process_wait.cancel()
        await asyncio.gather(self.process_wait, return_exceptions=True)

    async def _terminate(
        self,
        fake_psutil: FakePsutil,
        root_identity: ProcessIdentity | None = None,
    ) -> None:
        if root_identity is None:
            root_identity = capture_process_identity(
                self.process.pid, fake_psutil
            )
        with patch.object(_windows_process, "psutil", fake_psutil), patch(
            "code_agent.runtime._windows_process.asyncio.create_subprocess_exec",
            side_effect=AssertionError("taskkill must not be spawned"),
        ) as spawn:
            await _windows_process.terminate_process_tree(
                self.process, self.process_wait, root_identity
            )
        spawn.assert_not_called()
