from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
    build_restore_snapshot,
)
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class StreamingScandir:
    def __init__(self, entries: list[object]) -> None:
        self._entries = iter(entries)
        self.consumed = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self._entries)
        self.consumed += 1
        return entry


class VirtualEntry:
    def __init__(self, path: Path) -> None:
        self.path = str(path)


class RestoreTopologyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: bytes) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target


class TopologyConversionTests(RestoreTopologyTestCase):
    def test_checkpoint_file_replaces_planned_current_directory(self) -> None:
        self.write("a/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)

        try:
            self.editor.restore(restore)
        except WorkspaceError as error:
            self.fail(f"planned directory-to-file conversion failed: {error}")

        self.assertTrue((self.root / "a").is_file())
        self.assertEqual((self.root / "a").read_bytes(), b"checkpoint")

    def test_nested_directories_refresh_after_each_owned_removal(self) -> None:
        self.write("a/b/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))

        self.editor.restore(build_restore_snapshot(("a/b/current.py",), target))

        self.assertEqual((self.root / "a").read_bytes(), b"checkpoint")

    def test_checkpoint_directory_replaces_planned_current_file(self) -> None:
        self.write("a", b"current")
        target = WorkspaceSnapshot(
            (SnapshotEntry("a/checkpoint.py", b"checkpoint", True),)
        )
        restore = build_restore_snapshot(("a",), target)

        try:
            self.editor.restore(restore)
        except WorkspaceError as error:
            self.fail(f"planned file-to-directory conversion failed: {error}")

        self.assertTrue((self.root / "a").is_dir())
        self.assertEqual((self.root / "a/checkpoint.py").read_bytes(), b"checkpoint")

    def test_file_blocker_without_tombstone_is_rejected_before_mutation(self) -> None:
        blocker = self.write("a", b"unplanned")
        target = WorkspaceSnapshot(
            (SnapshotEntry("a/checkpoint.py", b"checkpoint", True),)
        )

        with self.assertRaisesRegex(WorkspaceError, "not planned"):
            self.editor.restore(target)

        self.assertEqual(blocker.read_bytes(), b"unplanned")


class TopologyConflictTests(RestoreTopologyTestCase):
    def test_directory_swap_after_owned_delete_is_rejected(self) -> None:
        self.write("a/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)
        from code_agent.workspace import _snapshot_restore as execution

        real_unlink = execution.secure_unlink

        def delete_then_swap(*args, **kwargs) -> None:
            real_unlink(*args, **kwargs)
            directory = self.root / "a"
            directory.rename(self.root / "original-a")
            directory.mkdir()
            (directory / "foreign.py").write_bytes(b"foreign")

        with patch.object(execution, "secure_unlink", side_effect=delete_then_swap):
            with self.assertRaisesRegex(WorkspaceError, "path changed"):
                self.editor.restore(restore)

        self.assertEqual((self.root / "a/foreign.py").read_bytes(), b"foreign")

    def test_late_unplanned_content_blocks_directory_removal(self) -> None:
        self.write("a/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)
        from code_agent.workspace import _snapshot_restore as execution

        real_unlink = execution.secure_unlink

        def delete_then_inject(*args, **kwargs) -> None:
            real_unlink(*args, **kwargs)
            (self.root / "a/late.py").write_bytes(b"late")

        with patch.object(execution, "secure_unlink", side_effect=delete_then_inject):
            with self.assertRaises(WorkspaceError):
                self.editor.restore(restore)

        self.assertEqual((self.root / "a/late.py").read_bytes(), b"late")

    def test_ignored_content_blocks_directory_to_file_conversion(self) -> None:
        planned = self.write("a/current.py", b"current")
        ignored = self.write("a/cache.tmp", b"ignored")
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)

        with self.assertRaisesRegex(WorkspaceError, "unplanned directory content"):
            self.editor.restore(restore)

        self.assertEqual(planned.read_bytes(), b"current")
        self.assertEqual(ignored.read_bytes(), b"ignored")

    def test_sensitive_content_blocks_directory_to_file_conversion(self) -> None:
        planned = self.write("a/current.py", b"current")
        secret = self.write("a/.env", b"secret")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)

        with self.assertRaisesRegex(WorkspaceError, "unplanned directory content"):
            self.editor.restore(restore)

        self.assertEqual(planned.read_bytes(), b"current")
        self.assertEqual(secret.read_bytes(), b"secret")

    def test_unplanned_nested_content_blocks_conversion_before_any_delete(self) -> None:
        planned = self.write("a/current.py", b"current")
        extra = self.write("a/nested/extra.py", b"extra")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)

        with self.assertRaisesRegex(WorkspaceError, "unplanned directory content"):
            self.editor.restore(restore)

        self.assertEqual(planned.read_bytes(), b"current")
        self.assertEqual(extra.read_bytes(), b"extra")


class TopologyScanLimitTests(RestoreTopologyTestCase):
    def test_large_directory_streams_until_entry_limit_before_mutation(self) -> None:
        files = [self.write(f"a/{index}.py", b"current") for index in range(5)]
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(
            (path.relative_to(self.root).as_posix() for path in files), target
        )
        with os.scandir(self.root / "a") as stream:
            fake_scan = StreamingScandir(list(stream))
        from code_agent.workspace import _restore_topology as topology

        with patch.object(topology, "MAX_ENTRIES", 3, create=True):
            with patch.object(topology.os, "scandir", return_value=fake_scan):
                with self.assertRaisesRegex(WorkspaceError, "entry limit"):
                    self.editor.restore(restore)

        self.assertEqual(fake_scan.consumed, 4)
        self.assertTrue(fake_scan.closed)
        self.assertTrue(all(path.read_bytes() == b"current" for path in files))

    def test_deep_directory_tree_does_not_use_python_recursion(self) -> None:
        from code_agent.workspace import _restore_topology as topology
        from code_agent.workspace import _secure_io as safety

        directory = self.root / "a"
        directory.mkdir()
        guard = WorkspacePathGuard(self.root)
        depth = 1_100
        scan_calls = 0
        identity = safety.PathIdentity(1, 1, stat.S_IFDIR | 0o755, 0, 0, 0)

        def resolve(path: object, *, for_write: bool = False) -> Path:
            del for_write
            candidate = Path(path)
            return candidate if candidate.is_absolute() else self.root / candidate

        def scan(path: object) -> StreamingScandir:
            nonlocal scan_calls
            scan_calls += 1
            entries = (
                []
                if scan_calls > depth
                else [VirtualEntry(directory / "virtual")]
            )
            return StreamingScandir(entries)

        entry = SnapshotEntry("a", b"checkpoint", True)
        with patch.object(guard, "resolve", side_effect=resolve):
            with patch.object(safety, "_inspect_path", return_value=identity):
                with patch.object(topology.time, "monotonic", return_value=0.0):
                    with patch.object(topology.os, "scandir", side_effect=scan):
                        try:
                            plan = topology.analyze_topology((entry,), guard)
                        except RecursionError as error:
                            self.fail(f"topology scan must be iterative: {error}")

        self.assertEqual(scan_calls, depth + 1)
        self.assertGreaterEqual(len(plan.directories), 2)

    def test_topology_deadline_expires_before_the_first_mutation(self) -> None:
        planned = self.write("a/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)
        from code_agent.workspace import _restore_topology as topology

        calls = 0

        def clock() -> float:
            nonlocal calls
            calls += 1
            return 0.0 if calls <= 3 else 2.0

        with patch.object(topology, "TOPOLOGY_SCAN_DEADLINE_S", 1.0, create=True):
            with patch.object(
                topology, "time", SimpleNamespace(monotonic=clock), create=True
            ):
                with self.assertRaisesRegex(WorkspaceError, "deadline"):
                    self.editor.restore(restore)

        self.assertEqual(planned.read_bytes(), b"current")

    def test_scandir_iteration_error_fails_closed(self) -> None:
        planned = self.write("a/current.py", b"current")
        target = WorkspaceSnapshot((SnapshotEntry("a", b"checkpoint", True),))
        restore = build_restore_snapshot(("a/current.py",), target)
        from code_agent.workspace import _restore_topology as topology

        class BrokenScandir(StreamingScandir):
            def __next__(self):
                raise OSError("denied")

        broken = BrokenScandir([])
        with patch.object(topology.os, "scandir", return_value=broken):
            with self.assertRaisesRegex(WorkspaceError, "inspect restore directory"):
                self.editor.restore(restore)

        self.assertTrue(broken.closed)
        self.assertEqual(planned.read_bytes(), b"current")


if __name__ == "__main__":
    unittest.main()
