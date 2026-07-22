from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace.errors import (  # noqa: E402
    FileTooLargeError,
    WorkspaceScanLimitError,
)
from code_agent.workspace.snapshot_store import (  # noqa: E402
    ContentAddressedSnapshotStore,
    SnapshotIntegrityError,
    SnapshotManifest,
    SnapshotManifestEntry,
)


class SnapshotStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def store(self, **limits: object) -> ContentAddressedSnapshotStore:
        return ContentAddressedSnapshotStore(self.root / "store", **limits)


class SnapshotStoreRoundTripTests(SnapshotStoreTestCase):
    def test_identical_content_is_stored_once_and_materializes(self) -> None:
        store = self.store()
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("two.py", b"same", True),
                SnapshotEntry("one.py", b"same", True),
            )
        )

        manifest = store.put(snapshot, {"one.py": 0o644, "two.py": 0o644})

        self.assertEqual(tuple(e.relative_path for e in manifest.entries), ("one.py", "two.py"))
        self.assertEqual(manifest.entries[0].blob_sha256, manifest.entries[1].blob_sha256)
        blobs = tuple(path for path in (self.root / "store" / "blobs").rglob("*") if path.is_file())
        self.assertEqual(len(blobs), 1)
        materialized = store.materialize(manifest)
        self.assertEqual(materialized.snapshot.entries, tuple(sorted(snapshot.entries, key=lambda e: e.relative_path)))
        self.assertEqual(dict(materialized.modes), {"one.py": 0o644, "two.py": 0o644})

    def test_tombstone_has_no_blob_size_or_mode(self) -> None:
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("gone.py", None, False),
                SnapshotEntry("kept.py", b"ok", True),
            )
        )

        manifest = self.store().put(snapshot, {"kept.py": 0o755})

        tombstone = manifest.entries[0]
        self.assertEqual((tombstone.relative_path, tombstone.existed), ("gone.py", False))
        self.assertEqual((tombstone.blob_sha256, tombstone.size, tombstone.mode), (None, 0, None))

    def test_manifest_digest_is_deterministic_across_input_order(self) -> None:
        first = WorkspaceSnapshot(
            (SnapshotEntry("b.py", b"b", True), SnapshotEntry("a.py", None, False))
        )
        second = WorkspaceSnapshot(tuple(reversed(first.entries)))

        first_manifest = self.store().put(first, {"b.py": 0o600})
        second_manifest = self.store().put(second, {"b.py": 0o600})

        self.assertEqual(first_manifest, second_manifest)

    def test_inventory_digest_matches_workspace_inventory_shape(self) -> None:
        content = b"kept"
        digest = hashlib.sha256(content).hexdigest()
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("gone.py", None, False), SnapshotEntry("kept.py", content, True))
        )
        encoded = json.dumps(
            [["kept.py", len(content), digest, 0o640]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        manifest = self.store().put(snapshot, {"kept.py": 0o640})

        self.assertEqual(manifest.inventory_digest, hashlib.sha256(encoded).hexdigest())

    def test_duplicate_canonical_paths_are_rejected_before_publish(self) -> None:
        store = self.store()
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("same.py", b"a", True), SnapshotEntry("same.py", b"b", True))
        )

        with self.assertRaisesRegex(ValueError, "duplicate snapshot path"):
            store.put(snapshot, {"same.py": 0o644})

        self.assertFalse((self.root / "store" / "blobs").exists())

    def test_noncanonical_paths_are_rejected(self) -> None:
        for path in (
            "../escape.py",
            "/absolute.py",
            "C:/absolute.py",
            "C:drive-relative.py",
            "folder\\file.py",
            "./file.py",
            "",
        ):
            with self.subTest(path=path):
                snapshot = WorkspaceSnapshot((SnapshotEntry(path, b"bad", True),))
                with self.assertRaises((TypeError, ValueError)):
                    self.store().put(snapshot, {path: 0o644})

    def test_modes_must_exactly_match_existing_entries(self) -> None:
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("live.py", b"ok", True), SnapshotEntry("gone.py", None, False))
        )

        for modes in ({}, {"live.py": 0o644, "gone.py": 0o644}, {"other.py": 0o644}):
            with self.subTest(modes=modes):
                with self.assertRaisesRegex(ValueError, "modes"):
                    self.store().put(snapshot, modes)


class SnapshotStoreLimitTests(SnapshotStoreTestCase):
    def test_file_count_limit_fails_before_publish(self) -> None:
        store = self.store(max_files=1)
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("a", b"a", True), SnapshotEntry("b", b"b", True))
        )

        with self.assertRaises(WorkspaceScanLimitError):
            store.put(snapshot, {"a": 0o600, "b": 0o600})
        self.assertFalse((self.root / "store" / "blobs").exists())

    def test_per_file_limit_fails_before_publish(self) -> None:
        store = self.store(max_file_bytes=2)

        with self.assertRaises(FileTooLargeError):
            store.put(WorkspaceSnapshot((SnapshotEntry("a", b"abc", True),)), {"a": 0o600})
        self.assertFalse((self.root / "store" / "blobs").exists())

    def test_total_byte_limit_fails_before_publish(self) -> None:
        store = self.store(max_total_bytes=3)
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry("a", b"aa", True), SnapshotEntry("b", b"bb", True))
        )

        with self.assertRaises(FileTooLargeError):
            store.put(snapshot, {"a": 0o600, "b": 0o600})
        self.assertFalse((self.root / "store" / "blobs").exists())


class SnapshotMaterializeValidationTests(SnapshotStoreTestCase):
    def test_corrupt_blob_has_a_clear_integrity_error(self) -> None:
        store = self.store()
        manifest = store.put(
            WorkspaceSnapshot((SnapshotEntry("a.py", b"ok", True),)),
            {"a.py": 0o644},
        )
        store.blob_path(manifest.entries[0].blob_sha256).write_bytes(b"bad")

        with self.assertRaisesRegex(SnapshotIntegrityError, "a.py.*(size|digest)"):
            store.materialize(manifest)

    def test_manifest_digest_is_verified_before_blob_reads(self) -> None:
        store = self.store()
        manifest = store.put(
            WorkspaceSnapshot((SnapshotEntry("a.py", b"ok", True),)),
            {"a.py": 0o644},
        )
        corrupt = replace(manifest, inventory_digest="0" * 64)
        store.blob_path(manifest.entries[0].blob_sha256).unlink()

        with self.assertRaisesRegex(SnapshotIntegrityError, "manifest digest"):
            store.materialize(corrupt)

    def test_manifest_total_and_entry_shapes_are_verified(self) -> None:
        digest = "0" * 64
        bad_entries = (
            SnapshotManifestEntry("gone.py", False, digest, 0, None),
            SnapshotManifestEntry("live.py", True, None, 0, 0o644),
        )
        for entry in bad_entries:
            with self.subTest(entry=entry):
                manifest = SnapshotManifest((entry,), digest, 0)
                with self.assertRaises(SnapshotIntegrityError):
                    self.store().materialize(manifest)

    def test_manifest_limits_are_rechecked_before_blob_reads(self) -> None:
        writer = self.store()
        manifest = writer.put(
            WorkspaceSnapshot((SnapshotEntry("a.py", b"abc", True),)),
            {"a.py": 0o600},
        )

        with self.assertRaises(FileTooLargeError):
            self.store(max_total_bytes=2).materialize(manifest)

    @unittest.skipUnless(os.name == "nt", "Windows mode mapping is platform-neutral")
    def test_windows_materialize_preserves_posix_mode_bits(self) -> None:
        store = self.store()
        manifest = store.put(
            WorkspaceSnapshot((SnapshotEntry("tool.py", b"x", True),)),
            {"tool.py": 0o751},
        )

        self.assertEqual(dict(store.materialize(manifest).modes), {"tool.py": 0o751})


if __name__ == "__main__":
    unittest.main()
