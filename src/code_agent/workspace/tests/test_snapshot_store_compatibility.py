from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace._snapshot_store_dirs import (  # noqa: E402
    BlobIntegrityFailure,
)
from code_agent.workspace.snapshot_store import (  # noqa: E402
    ContentAddressedSnapshotStore,
    SnapshotIntegrityError,
)


def _snapshot(*entries: tuple[str, bytes]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        tuple(SnapshotEntry(path, content, True) for path, content in entries)
    )


def _tree_state(root: Path) -> tuple[tuple[str, bytes, int], ...]:
    if not root.exists():
        return ()
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


class SnapshotStoreCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.primary_root = self.root / "primary"
        self.legacy_root = self.root / "legacy"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _legacy_manifest(self, snapshot: WorkspaceSnapshot):
        modes = {
            entry.relative_path: 0o644
            for entry in snapshot.entries
            if entry.existed
        }
        return ContentAddressedSnapshotStore(self.legacy_root).put(snapshot, modes)

    def _compatible(self) -> ContentAddressedSnapshotStore:
        return ContentAddressedSnapshotStore(
            self.primary_root, read_fallback_roots=(self.legacy_root,)
        )

    def _symlink_or_skip(
        self, link: Path, target: Path, *, target_is_directory: bool = False
    ) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")

    def test_missing_primary_reads_legacy_without_mutating_either_root(self) -> None:
        snapshot = _snapshot(("note.py", b"legacy bytes"))
        manifest = self._legacy_manifest(snapshot)
        legacy_before = _tree_state(self.legacy_root)

        materialized = self._compatible().materialize(manifest)

        self.assertEqual(materialized.snapshot, snapshot)
        self.assertFalse(self.primary_root.exists())
        self.assertEqual(_tree_state(self.legacy_root), legacy_before)

    def test_valid_primary_wins_without_reading_corrupt_legacy(self) -> None:
        snapshot = _snapshot(("note.py", b"primary bytes"))
        manifest = self._legacy_manifest(snapshot)
        primary = ContentAddressedSnapshotStore(self.primary_root)
        primary.put(snapshot, {"note.py": 0o644})
        legacy_blob = ContentAddressedSnapshotStore(self.legacy_root).blob_path(
            manifest.entries[0].blob_sha256 or ""
        )
        legacy_blob.write_bytes(b"x" * len(b"primary bytes"))
        legacy_before = _tree_state(self.legacy_root)

        materialized = self._compatible().materialize(manifest)

        self.assertEqual(materialized.snapshot, snapshot)
        self.assertEqual(_tree_state(self.legacy_root), legacy_before)

    def test_corrupt_primary_fails_closed_instead_of_using_legacy(self) -> None:
        snapshot = _snapshot(("note.py", b"trusted"))
        manifest = self._legacy_manifest(snapshot)
        primary = ContentAddressedSnapshotStore(self.primary_root)
        primary.put(snapshot, {"note.py": 0o644})
        primary.blob_path(manifest.entries[0].blob_sha256 or "").write_bytes(
            b"x" * len(b"trusted")
        )

        with self.assertRaises(SnapshotIntegrityError):
            self._compatible().materialize(manifest)

    def test_linked_primary_blob_fails_closed_instead_of_using_legacy(self) -> None:
        snapshot = _snapshot(("note.py", b"trusted"))
        manifest = self._legacy_manifest(snapshot)
        primary = ContentAddressedSnapshotStore(self.primary_root)
        digest = manifest.entries[0].blob_sha256 or ""
        blob = primary.blob_path(digest)
        blob.parent.mkdir(parents=True)
        outside = self.root / "outside-blob"
        outside.write_bytes(b"trusted")
        self._symlink_or_skip(blob, outside)

        with self.assertRaises(SnapshotIntegrityError):
            self._compatible().materialize(manifest)

    def test_linked_fallback_root_is_rejected_at_admission(self) -> None:
        snapshot = _snapshot(("note.py", b"trusted"))
        self._legacy_manifest(snapshot)
        linked = self.root / "linked-legacy"
        self._symlink_or_skip(linked, self.legacy_root, target_is_directory=True)

        with self.assertRaises(SnapshotIntegrityError):
            ContentAddressedSnapshotStore(
                self.primary_root, read_fallback_roots=(linked,)
            )

    def test_reparse_fallback_root_is_rejected_deterministically(self) -> None:
        snapshot = _snapshot(("note.py", b"trusted"))
        self._legacy_manifest(snapshot)
        original_lstat = Path.lstat

        def marked_reparse(path: Path):
            metadata = original_lstat(path)
            if path == self.legacy_root:
                return SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_file_attributes=0x400,
                )
            return metadata

        with patch.object(Path, "lstat", new=marked_reparse):
            with self.assertRaises(SnapshotIntegrityError):
                ContentAddressedSnapshotStore(
                    self.primary_root,
                    read_fallback_roots=(self.legacy_root,),
                )

    def test_primary_read_failure_never_attempts_fallback(self) -> None:
        snapshot = _snapshot(("note.py", b"trusted"))
        manifest = self._legacy_manifest(snapshot)
        compatible = self._compatible()

        with patch(
            "code_agent.workspace.snapshot_store.read_blob_if_present",
            side_effect=BlobIntegrityFailure("primary identity changed"),
        ) as reader:
            with self.assertRaisesRegex(SnapshotIntegrityError, "identity"):
                compatible.materialize(manifest)

        self.assertEqual(reader.call_count, 1)

    def test_manifest_can_read_individual_blobs_from_different_roots(self) -> None:
        snapshot = _snapshot(("a.py", b"alpha"), ("b.py", b"beta"))
        manifest = self._legacy_manifest(snapshot)
        ContentAddressedSnapshotStore(self.primary_root).put(
            _snapshot(("a.py", b"alpha")), {"a.py": 0o644}
        )

        materialized = self._compatible().materialize(manifest)

        self.assertEqual(materialized.snapshot, snapshot)

    def test_put_and_gc_never_modify_fallback_root(self) -> None:
        legacy = _snapshot(("legacy.py", b"keep"))
        self._legacy_manifest(legacy)
        legacy_before = _tree_state(self.legacy_root)
        compatible = self._compatible()

        compatible.put(_snapshot(("new.py", b"new")), {"new.py": 0o644})
        compatible.delete_orphans((), datetime.now(timezone.utc).timestamp() + 1)

        self.assertEqual(_tree_state(self.legacy_root), legacy_before)

    def test_gc_helpers_receive_only_primary_blob_root(self) -> None:
        compatible = self._compatible()
        with patch(
            "code_agent.workspace.snapshot_store.collect_orphans",
            return_value=(),
        ) as collect, patch(
            "code_agent.workspace.snapshot_store.delete_candidates",
            return_value=(),
        ) as delete:
            compatible.delete_orphans(
                (), datetime.now(timezone.utc).timestamp() + 1
            )

        self.assertEqual(collect.call_args.args[0], compatible.blobs_root)
        self.assertEqual(delete.call_args.args[0], compatible.blobs_root)

    def test_all_roots_missing_preserves_single_root_failure(self) -> None:
        snapshot = _snapshot(("gone.py", b"gone"))
        manifest = self._legacy_manifest(snapshot)
        shutil.rmtree(self.legacy_root)
        single = ContentAddressedSnapshotStore(self.primary_root)

        with self.assertRaises(SnapshotIntegrityError) as expected:
            single.materialize(manifest)
        with self.assertRaises(SnapshotIntegrityError) as compatible:
            self._compatible().materialize(manifest)

        self.assertEqual(str(compatible.exception), str(expected.exception))
        self.assertFalse(self.primary_root.exists())
        self.assertFalse(self.legacy_root.exists())


if __name__ == "__main__":
    unittest.main()
