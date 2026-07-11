from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _windows_process  # noqa: E402
from code_agent.runtime.errors import RuntimeErrorBase  # noqa: E402
from code_agent.runtime.tests._process_tree_support import (  # noqa: E402
    FakeAccessDenied,
    FakeAsyncProcess,
    FakeNoSuchProcess,
    FakeProcess,
    FakePsutil,
    ProcessTreeTestCase,
)


class ProcessTerminationTests(ProcessTreeTestCase):
    async def test_no_such_root_is_already_exited_without_direct_kill(self) -> None:
        root = FakeProcess("root", self.process.pid, 10.0, self.events)
        process_api = FakePsutil(root, self.events)
        root_identity = _windows_process.capture_process_identity(
            self.process.pid, process_api
        )
        root._create_time_error = FakeNoSuchProcess()

        with patch.object(_windows_process, "_ROOT_WAIT_TIMEOUT_S", 0.01):
            with self.assertRaises(RuntimeErrorBase) as raised:
                await self._terminate(process_api, root_identity)

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertIn(("root_handle", "kill"), self.events)
        self.assertNotIn(("root", "suspend"), self.events)

    async def test_reused_root_pid_is_not_signaled_and_reports_error(self) -> None:
        old_root = FakeProcess("old_root", self.process.pid, 10.0, self.events)
        new_root = FakeProcess("new_root", self.process.pid, 20.0, self.events)
        process_api = FakePsutil(old_root, self.events)
        root_identity = _windows_process.capture_process_identity(
            self.process.pid, process_api
        )
        old_root._created = 20.0
        process_api._root = new_root

        with self.assertRaises(RuntimeErrorBase) as raised:
            await self._terminate(process_api, root_identity)

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertNotIn(("old_root", "suspend"), self.events)
        self.assertNotIn(("new_root", "suspend"), self.events)
        self.assertNotIn(("new_root", "kill"), self.events)
        self.assertEqual(
            self.events.count(("psutil", "Process", self.process.pid)), 1
        )

    async def test_access_denied_reports_error_after_direct_root_cleanup(self) -> None:
        child = FakeProcess(
            "child",
            7001,
            11.0,
            self.events,
            suspend_error=FakeAccessDenied(),
        )
        root = FakeProcess(
            "root", self.process.pid, 10.0, self.events,
            children_rounds=[[child], [child]],
        )

        with self.assertRaises(RuntimeErrorBase) as raised:
            await self._terminate(FakePsutil(root, self.events))

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertEqual(raised.exception.pid, self.process.pid)
        self.assertTrue(raised.exception.failures)
        self.assertIn(("root_handle", "kill"), self.events)

    async def test_survivor_after_second_wait_reports_error(self) -> None:
        child = FakeProcess("child", 7001, 11.0, self.events)
        root = FakeProcess(
            "root", self.process.pid, 10.0, self.events,
            children_rounds=[[child], [child]],
        )
        fake_psutil = FakePsutil(
            root,
            self.events,
            wait_results=[([], [child]), ([], [child])],
        )

        with self.assertRaises(RuntimeErrorBase) as raised:
            await self._terminate(fake_psutil)

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertEqual(self.events.count(("child", "kill")), 2)
        self.assertEqual(
            len([event for event in self.events if event[:2] == ("psutil", "wait_procs")]),
            2,
        )
        self.assertTrue(any("survived" in item for item in raised.exception.failures))

    async def test_thread_deadline_failure_still_kills_root_and_is_reported(self) -> None:
        root = FakeProcess("root", self.process.pid, 10.0, self.events)

        async def blocked_to_thread(*args: object, **kwargs: object) -> object:
            del args, kwargs
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        with patch.object(_windows_process.asyncio, "to_thread", blocked_to_thread), patch.object(
            _windows_process, "_THREAD_CALL_TIMEOUT_S", 0.01
        ):
            with self.assertRaises(RuntimeErrorBase) as raised:
                await asyncio.wait_for(
                    self._terminate(FakePsutil(root, self.events)), timeout=0.2
                )

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertIn(("root_handle", "kill"), self.events)
        self.assertTrue(any("deadline" in item for item in raised.exception.failures))

    async def test_root_wait_deadline_is_reported_after_direct_kill(self) -> None:
        self.process_wait.cancel()
        await asyncio.gather(self.process_wait, return_exceptions=True)
        self.process = FakeAsyncProcess(self.events, complete_after_kill=False)
        self.process_wait = asyncio.create_task(self.process.wait())
        root = FakeProcess("root", self.process.pid, 10.0, self.events)

        with patch.object(_windows_process, "_ROOT_WAIT_TIMEOUT_S", 0.01):
            with self.assertRaises(RuntimeErrorBase) as raised:
                await self._terminate(FakePsutil(root, self.events))

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertIn(("root_handle", "kill"), self.events)
        self.assertTrue(any("root process" in item for item in raised.exception.failures))


if __name__ == "__main__":
    unittest.main()
