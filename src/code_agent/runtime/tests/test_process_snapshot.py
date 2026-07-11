from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _process_snapshot, _windows_process  # noqa: E402
from code_agent.runtime.errors import RuntimeErrorBase  # noqa: E402
from code_agent.runtime.tests._process_tree_support import (  # noqa: E402
    FakeNoSuchProcess,
    FakeProcess,
    FakePsutil,
    ProcessTreeTestCase,
)


class ProcessSnapshotTests(ProcessTreeTestCase):
    def test_capture_process_identity_binds_object_and_creation_time(self) -> None:
        root = FakeProcess("root", self.process.pid, 10.0, self.events)
        process_api = FakePsutil(root, self.events)

        identity = _process_snapshot.capture_process_identity(
            self.process.pid, process_api
        )

        self.assertIs(identity.process, root)
        self.assertEqual(identity.create_time, 10.0)
        self.assertEqual(
            self.events[:2],
            [
                ("psutil", "Process", self.process.pid),
                ("root", "create_time"),
            ],
        )

    def test_freeze_uses_startup_identity_without_rebinding_pid(self) -> None:
        old_root = FakeProcess("old_root", self.process.pid, 10.0, self.events)
        new_root = FakeProcess("new_root", self.process.pid, 20.0, self.events)
        identity = _process_snapshot.ProcessIdentity(old_root, 10.0)
        old_root._created = 20.0
        process_api = FakePsutil(new_root, self.events)

        frozen = _process_snapshot.freeze_process_tree(
            identity,
            process_api=process_api,
            max_processes=1024,
        )

        self.assertTrue(frozen.failures)
        self.assertNotIn(("new_root", "suspend"), self.events)
        self.assertNotIn(("new_root", "children", True), self.events)
        self.assertFalse(any(event[:2] == ("psutil", "Process") for event in self.events))

    def test_resume_rechecks_identity_before_resuming_process(self) -> None:
        root = FakeProcess("root", self.process.pid, 10.0, self.events)
        process_api = FakePsutil(root, self.events)
        identity = _process_snapshot.capture_process_identity(
            self.process.pid, process_api
        )

        _process_snapshot.resume_process_identity(identity, process_api)

        self.assertEqual(self.events[-2:], [
            ("root", "create_time"),
            ("root", "resume"),
        ])

    async def test_freezes_repeated_discovery_then_kills_descendants_in_reverse(self) -> None:
        child_one = FakeProcess("child_one", 7001, 11.0, self.events)
        child_two = FakeProcess("child_two", 7002, 12.0, self.events)
        root = FakeProcess(
            "root",
            self.process.pid,
            10.0,
            self.events,
            children_rounds=[
                [child_one],
                [child_one, child_two],
                [child_one, child_two],
            ],
        )
        fake_psutil = FakePsutil(root, self.events)

        await self._terminate(fake_psutil)

        self.assertLess(
            self.events.index(("root", "suspend")),
            self.events.index(("root", "children", True)),
        )
        self.assertEqual(self.events.count(("root", "children", True)), 3)
        self.assertLess(
            self.events.index(("root_handle", "kill")),
            self.events.index(("child_two", "kill")),
        )
        descendant_kills = [
            event for event in self.events if event in {
                ("child_one", "kill"), ("child_two", "kill")
            }
        ]
        self.assertEqual(
            descendant_kills,
            [("child_two", "kill"), ("child_one", "kill")],
        )
        wait_events = [event for event in self.events if event[:2] == ("psutil", "wait_procs")]
        self.assertEqual(len(wait_events), 1)
        self.assertGreater(wait_events[0][-1], 0)

    async def test_changed_create_time_is_not_killed_as_reused_pid(self) -> None:
        child = FakeProcess(
            "child",
            7001,
            11.0,
            self.events,
            identity_after_root_kill=99.0,
        )
        root = FakeProcess(
            "root", self.process.pid, 10.0, self.events,
            children_rounds=[[child], [child]],
        )

        await self._terminate(FakePsutil(root, self.events))

        self.assertNotIn(("child", "kill"), self.events)
        self.assertFalse(any(event[:2] == ("psutil", "wait_procs") for event in self.events))

    async def test_no_such_process_during_identity_check_is_already_exited(self) -> None:
        child = FakeProcess(
            "child",
            7001,
            11.0,
            self.events,
            identity_after_root_kill=FakeNoSuchProcess(),
        )
        root = FakeProcess(
            "root", self.process.pid, 10.0, self.events,
            children_rounds=[[child], [child]],
        )

        await self._terminate(FakePsutil(root, self.events))

        self.assertNotIn(("child", "kill"), self.events)

    async def test_process_tree_limit_reports_error_instead_of_partial_success(self) -> None:
        children = [
            FakeProcess(f"child_{index}", 7001 + index, 11.0 + index, self.events)
            for index in range(2)
        ]
        root = FakeProcess(
            "root", self.process.pid, 10.0, self.events,
            children_rounds=[children],
        )

        with patch.object(_windows_process, "_MAX_PROCESS_TREE_SIZE", 2):
            with self.assertRaises(RuntimeErrorBase) as raised:
                await self._terminate(FakePsutil(root, self.events))

        self.assertEqual(type(raised.exception).__name__, "ProcessTreeTerminationError")
        self.assertTrue(any("limit" in item for item in raised.exception.failures))
        self.assertIn(("root_handle", "kill"), self.events)


if __name__ == "__main__":
    unittest.main()
