from __future__ import annotations

import os
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
from code_agent.workspace.errors import (  # noqa: E402
    FileTooLargeError,
    WorkspaceError,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RestoreSecurityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()


class MissingParentRestoreTests(RestoreSecurityTestCase):
    def test_restore_recreates_a_deleted_parent_tree(self) -> None:
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("package/nested/module.py", b"restored", True),)
        )

        try:
            self.editor.restore(snapshot)
        except WorkspaceError as error:
            self.fail(f"restore should safely recreate missing parents: {error}")

        self.assertEqual(
            (self.root / "package/nested/module.py").read_bytes(), b"restored"
        )

    def test_missing_tombstone_parent_is_a_no_op(self) -> None:
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("already-gone/file.py", None, False),)
        )

        try:
            self.editor.restore(snapshot)
        except WorkspaceError as error:
            self.fail(f"missing tombstone should be a no-op: {error}")

        self.assertFalse((self.root / "already-gone").exists())


class RestorePreflightTests(RestoreSecurityTestCase):
    def test_aggregate_limit_fails_before_the_first_write(self) -> None:
        first = self.root / "first.py"
        first.write_bytes(b"before")
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("first.py", b"123", True),
                SnapshotEntry("second.py", b"456", True),
            )
        )

        try:
            with self.assertRaises(FileTooLargeError):
                self.editor.restore(snapshot, max_total_bytes=5)
        except TypeError as error:
            self.fail(f"restore must expose an aggregate byte limit: {error}")

        self.assertEqual(first.read_bytes(), b"before")
        self.assertFalse((self.root / "second.py").exists())

    def test_insufficient_disk_space_fails_before_the_first_write(self) -> None:
        first = self.root / "first.py"
        first.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry("first.py", b"after", True),))

        with patch("shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(WorkspaceError, "disk space"):
                self.editor.restore(snapshot)

        self.assertEqual(first.read_bytes(), b"before")

    def test_unwritable_nearest_ancestor_fails_without_creating_parents(self) -> None:
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("missing/nested/file.py", b"content", True),)
        )

        with patch("os.access", return_value=False):
            with self.assertRaisesRegex(WorkspaceError, "not writable"):
                self.editor.restore(snapshot)

        self.assertFalse((self.root / "missing").exists())

    def test_current_path_case_semantics_follow_the_platform(self) -> None:
        if os.name == "nt":
            with self.assertRaisesRegex(ValueError, "duplicate current path"):
                build_restore_snapshot(("Foo.py", "foo.py"), WorkspaceSnapshot(()))
        else:
            restore = build_restore_snapshot(("Foo.py", "foo.py"), WorkspaceSnapshot(()))
            self.assertEqual(len(restore.entries), 2)

    def test_target_path_case_semantics_follow_the_platform(self) -> None:
        target = WorkspaceSnapshot(
            (
                SnapshotEntry("Foo.py", b"one", True),
                SnapshotEntry("foo.py", b"two", True),
            )
        )

        if os.name == "nt":
            with self.assertRaisesRegex(ValueError, "duplicate target path"):
                build_restore_snapshot((), target)
        else:
            self.assertEqual(len(build_restore_snapshot((), target).entries), 2)


class RestoreRaceTests(RestoreSecurityTestCase):
    def test_replace_failure_cleans_owned_restore_temp(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry("module.py", b"after", True),))
        before_names = {path.name for path in self.root.iterdir()}

        with patch("os.replace", side_effect=OSError("busy")):
            with self.assertRaisesRegex(WorkspaceError, "atomically restore"):
                self.editor.restore(snapshot)

        self.assertEqual(target.read_bytes(), b"before")
        self.assertEqual({path.name for path in self.root.iterdir()}, before_names)

    def test_parent_replacement_during_temp_creation_fails_closed(self) -> None:
        parent = self.root / "package"
        displaced = self.root / "original-package"
        parent.mkdir()
        target = parent / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("package/module.py", b"after", True),)
        )
        real_named_temporary = tempfile.NamedTemporaryFile
        attacked = False

        def replace_parent(*args: object, **kwargs: object):
            nonlocal attacked
            if not attacked:
                attacked = True
                parent.rename(displaced)
                parent.mkdir()
            return real_named_temporary(*args, **kwargs)

        if os.name == "posix":
            from code_agent.workspace import _posix_io

            real_create_temp = _posix_io.create_temp

            def replace_before_create(parent_fd: int, name: str) -> int:
                nonlocal attacked
                if not attacked:
                    attacked = True
                    parent.rename(displaced)
                    parent.mkdir()
                return real_create_temp(parent_fd, name)

            creator = patch.object(
                _posix_io, "create_temp", side_effect=replace_before_create
            )
        else:
            creator = patch("tempfile.NamedTemporaryFile", side_effect=replace_parent)
        with creator:
            with self.assertRaisesRegex(
                WorkspaceError, "(changed|disappeared) during restore"
            ):
                self.editor.restore(snapshot)

        self.assertEqual((displaced / "module.py").read_bytes(), b"before")
        self.assertFalse((parent / "module.py").exists())


if __name__ == "__main__":
    unittest.main()
