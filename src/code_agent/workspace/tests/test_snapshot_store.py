from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import FileTooLargeError, WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.snapshot_store import (  # noqa: E402
    SnapshotHandle,
    WorkspaceSnapshotStore,
)


class SnapshotStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "workspace"
        self.artifacts = self.base / "product-state"
        self.root.mkdir()
        self.guard = WorkspacePathGuard(self.root)
        self.editor = WorkspaceEditor(self.guard)
        self.store = WorkspaceSnapshotStore(self.guard, self.artifacts)

    def tearDown(self) -> None:
        self.temporary.cleanup()


class SnapshotStoreRoundTripTests(SnapshotStoreTestCase):
    def test_persists_dirty_missing_and_binary_bytes_for_restore(self) -> None:
        dirty = self.root / "dirty.bin"
        deleted = self.root / "deleted.txt"
        future = self.root / "future.txt"
        dirty.write_bytes(b"\x00dirty\xff")
        deleted.write_bytes(b"delete me")
        snapshot = self.editor.snapshot(("dirty.bin", "deleted.txt", "future.txt"))

        handle = self.store.save(snapshot)
        loaded = self.store.load(handle)

        self.assertEqual(handle.paths, ("dirty.bin", "deleted.txt", "future.txt"))
        self.assertEqual(handle.total_bytes, len(b"\x00dirty\xffdelete me"))
        expected_blobs = {
            hashlib.sha256(b"\x00dirty\xff").hexdigest(),
            hashlib.sha256(b"delete me").hexdigest(),
        }
        self.assertEqual({path.name for path in (self.artifacts / "blobs").iterdir()}, expected_blobs)
        self.assertTrue((self.artifacts / "manifests" / f"{handle.identifier}.json").is_file())

        dirty.write_bytes(b"changed")
        deleted.unlink()
        future.write_bytes(b"created later")
        self.editor.restore(loaded)

        self.assertEqual(dirty.read_bytes(), b"\x00dirty\xff")
        self.assertEqual(deleted.read_bytes(), b"delete me")
        self.assertFalse(future.exists())
        self.assertEqual(loaded, snapshot)

    def test_repeated_content_reuses_content_addressed_blob(self) -> None:
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("one.bin", b"same", True),
                SnapshotEntry("two.bin", b"same", True),
            )
        )

        first = self.store.save(snapshot)
        second = self.store.save(snapshot)

        self.assertNotEqual(first.identifier, second.identifier)
        self.assertEqual(len(tuple((self.artifacts / "blobs").iterdir())), 1)


class SnapshotHandleTests(unittest.TestCase):
    def test_handle_is_frozen_and_has_strict_json_round_trip(self) -> None:
        handle = SnapshotHandle("a" * 32, "b" * 64, ("file.bin",), 4)
        payload = {
            "identifier": "a" * 32,
            "digest": "b" * 64,
            "paths": ["file.bin"],
            "total_bytes": 4,
        }

        self.assertEqual(handle.to_dict(), payload)
        self.assertEqual(SnapshotHandle.from_dict(payload), handle)
        with self.assertRaises(FrozenInstanceError):
            handle.digest = "c" * 64  # type: ignore[misc]

        invalid_payloads = (
            {**payload, "extra": True},
            {**payload, "paths": ("file.bin",)},
            {**payload, "total_bytes": True},
            {**payload, "identifier": "../manifest"},
            {**payload, "digest": "not-a-digest"},
        )
        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    SnapshotHandle.from_dict(invalid)


class SnapshotStoreBoundaryTests(SnapshotStoreTestCase):
    def test_rejects_duplicate_and_noncanonical_snapshot_paths(self) -> None:
        snapshots = (
            WorkspaceSnapshot(
                (
                    SnapshotEntry("same.bin", b"one", True),
                    SnapshotEntry("same.bin", b"two", True),
                )
            ),
            WorkspaceSnapshot((SnapshotEntry("dir/../same.bin", b"one", True),)),
        )
        for snapshot in snapshots:
            with self.subTest(paths=[entry.relative_path for entry in snapshot.entries]):
                with self.assertRaises((ValueError, WorkspaceError)):
                    self.store.save(snapshot)

    def test_total_byte_limit_is_exact(self) -> None:
        snapshot = WorkspaceSnapshot((SnapshotEntry("three.bin", b"123", True),))

        handle = WorkspaceSnapshotStore(
            self.guard, self.base / "exact", max_total_bytes=3
        ).save(snapshot)

        self.assertEqual(handle.total_bytes, 3)
        with self.assertRaises(FileTooLargeError):
            WorkspaceSnapshotStore(
                self.guard, self.base / "too-small", max_total_bytes=2
            ).save(snapshot)

    def test_artifact_root_must_be_absolute_and_outside_workspace(self) -> None:
        for root in (Path("relative-state"), self.root / "state", self.root / ".git" / "state"):
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    WorkspaceSnapshotStore(self.guard, root)

    def test_authorized_local_config_state_root_round_trips(self) -> None:
        local_app_data = self.base / "local-app-data"
        product_state = local_app_data / "chaos-agent" / "snapshots"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}, clear=False):
            guard = WorkspacePathGuard(self.root)
            editor = WorkspaceEditor(guard)
            store = WorkspaceSnapshotStore(guard, product_state)
            (self.root / "authorized.bin").write_bytes(b"authorized")
            snapshot = editor.snapshot(("authorized.bin",))

            handle = store.save(snapshot)

            self.assertEqual(store.load(handle), snapshot)

    def test_external_git_artifact_roots_are_always_rejected(self) -> None:
        roots = (
            self.base / "outside" / ".git",
            self.base / "outside" / ".GiT" / "snapshots",
        )
        for root in roots:
            with self.subTest(root=root):
                with self.assertRaises((ValueError, WorkspaceError)):
                    WorkspaceSnapshotStore(self.guard, root)

    def test_artifact_root_rejects_link_like_parent_components(self) -> None:
        parent = self.base / "linked-parent"
        parent.mkdir()
        with patch(
            "code_agent.workspace._snapshot_artifacts._is_link_like",
            side_effect=lambda path: path == parent,
        ):
            with self.assertRaises(WorkspaceError):
                WorkspaceSnapshotStore(self.guard, parent / "snapshots")

    def test_absolute_sensitive_and_traversal_paths_fail_closed(self) -> None:
        for path in (str(self.root / "absolute.bin"), ".env", "../outside.bin"):
            with self.subTest(path=path):
                snapshot = WorkspaceSnapshot((SnapshotEntry(path, b"secret", True),))
                with self.assertRaises(WorkspaceError):
                    self.store.save(snapshot)


if __name__ == "__main__":
    unittest.main()
