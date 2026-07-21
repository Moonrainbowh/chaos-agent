from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
