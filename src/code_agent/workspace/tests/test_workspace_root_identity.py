from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import code_agent.workspace._guarded_read as guarded_read  # noqa: E402
from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace.errors import (  # noqa: E402
    SnapshotIntegrityError,
    SnapshotMissingError,
    WorkspaceError,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle semantics")
class WorkspaceRootIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_guard_records_read_only_native_root_identity(self) -> None:
        guard = WorkspacePathGuard(self.root)

        self.assertTrue(hasattr(guard, "root_identity"))
        self.assertEqual(len(guard.root_identity), 2)
        self.assertTrue(all(type(value) is int for value in guard.root_identity))
        with self.assertRaises(AttributeError):
            guard.root_identity = (0, 0)  # type: ignore[misc]
        with self.assertRaises(TypeError):
            guard.root_identity[0] = 0  # type: ignore[index]

    def test_workspace_parent_chain_starts_at_volume_anchor(self) -> None:
        target = self.root / "one" / "file.bin"

        parents = windows_open._parent_paths(target, self.root)

        self.assertEqual(parents[0], Path(target.anchor))
        self.assertIn(self.root, parents)
        self.assertEqual(parents[-1], target.parent)

    def test_same_path_root_replacement_rejects_old_guard_and_changes_fingerprint(
        self,
    ) -> None:
        target = self.root / "file.bin"
        target.write_bytes(b"original")
        old_guard = WorkspacePathGuard(self.root)
        old_store = WorkspaceSnapshotStore(old_guard, self.base / "state-old")
        moved = self.base / "workspace-old"
        self.root.rename(moved)
        self.root.mkdir()
        target.write_bytes(b"replacement")
        new_guard = WorkspacePathGuard(self.root)
        new_store = WorkspaceSnapshotStore(new_guard, self.base / "state-new")

        with self.assertRaises(WorkspaceError) as captured:
            guarded_read.read_guarded_file("file.bin", old_guard, 32)
        self.assertNotIsInstance(
            captured.exception, guarded_read._GuardedFileMissingError
        )
        self.assertNotEqual(old_guard.root_identity, new_guard.root_identity)
        self.assertNotEqual(
            old_store.workspace_fingerprint, new_store.workspace_fingerprint
        )

    def test_allow_outside_does_not_apply_workspace_root_identity(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        target = outside / "file.bin"
        target.write_bytes(b"external")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        self.root.rename(self.base / "workspace-old")
        self.root.mkdir()

        content = guarded_read.read_guarded_file(target, guard, 32)

        self.assertEqual(content, b"external")

    def test_replaced_artifact_root_is_integrity_not_missing(self) -> None:
        guard = WorkspacePathGuard(self.root)
        artifacts = self.base / "state"
        store = WorkspaceSnapshotStore(guard, artifacts)
        handle = store.save(
            WorkspaceSnapshot((SnapshotEntry("file.bin", b"bytes", True),))
        )
        artifacts.rename(self.base / "state-old")
        (artifacts / "manifests").mkdir(parents=True)
        (artifacts / "blobs").mkdir()

        with self.assertRaises(SnapshotIntegrityError) as captured:
            store.load(handle)

        self.assertNotIsInstance(captured.exception, SnapshotMissingError)


if __name__ == "__main__":
    unittest.main()
