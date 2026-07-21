from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace.errors import (  # noqa: E402
    SearchTimeoutError,
    WorkspaceScanLimitError,
)
from code_agent.workspace.snapshot_store import (  # noqa: E402
    ContentAddressedSnapshotStore,
    SnapshotIntegrityError,
    SnapshotManifest,
)


def snapshot(*entries: tuple[str, bytes]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        tuple(SnapshotEntry(path, content, True) for path, content in entries)
    )


class ReviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = ContentAddressedSnapshotStore(self.root / "store")

    def tearDown(self) -> None:
        self.temporary.cleanup()


class PlatformIndependentManifestTests(ReviewTestCase):
    def test_entries_and_inventory_digest_use_raw_normalized_path_order(self) -> None:
        contents = {"B.py": b"upper", "a.py": b"lower"}
        manifest = self.store.put(
            snapshot(*contents.items()), {"B.py": 0o640, "a.py": 0o600}
        )
        values = [
            [path, len(contents[path]), hashlib.sha256(contents[path]).hexdigest(), mode]
            for path, mode in (("B.py", 0o640), ("a.py", 0o600))
        ]
        encoded = json.dumps(
            values, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        self.assertEqual(tuple(entry.relative_path for entry in manifest.entries), ("B.py", "a.py"))
        self.assertEqual(manifest.inventory_digest, hashlib.sha256(encoded).hexdigest())

    def test_materialize_accepts_the_same_raw_order_on_every_platform(self) -> None:
        initial = self.store.put(
            snapshot(("a.py", b"lower"), ("B.py", b"upper")),
            {"a.py": 0o600, "B.py": 0o640},
        )
        by_path = {entry.relative_path: entry for entry in initial.entries}
        entries = (by_path["B.py"], by_path["a.py"])
        values = [
            [entry.relative_path, entry.size, entry.blob_sha256, entry.mode]
            for entry in entries
        ]
        digest = hashlib.sha256(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest = SnapshotManifest(entries, digest, initial.total_bytes)

        restored = self.store.materialize(manifest)

        self.assertEqual(
            tuple(entry.relative_path for entry in restored.snapshot.entries),
            ("B.py", "a.py"),
        )


class ReferencedBudgetTests(ReviewTestCase):
    def test_infinite_referenced_iterator_stops_at_entry_budget(self) -> None:
        digest = "0" * 64
        consumed = 0

        def references():
            nonlocal consumed
            while True:
                consumed += 1
                if consumed > 3:
                    raise AssertionError("referenced iterator was consumed past its budget")
                yield digest

        limited = ContentAddressedSnapshotStore(
            self.root / "missing-store", gc_max_entries=2
        )

        with self.assertRaises(WorkspaceScanLimitError):
            limited.delete_orphans(references(), older_than=time.time())
        self.assertEqual(consumed, 3)

    def test_referenced_iterator_uses_the_gc_deadline(self) -> None:
        current = [0.0]

        def references():
            yield "0" * 64
            current[0] = 2.0
            yield "1" * 64

        limited = ContentAddressedSnapshotStore(
            self.root / "missing-store", gc_deadline_s=1.0
        )
        with patch(
            "code_agent.workspace.snapshot_store.time.monotonic",
            side_effect=lambda: current[0],
        ):
            with self.assertRaises(SearchTimeoutError):
                limited.delete_orphans(references(), older_than=time.time())

    def test_references_and_store_entries_share_one_budget(self) -> None:
        manifest = self.store.put(snapshot(("file", b"orphan")), {"file": 0o600})
        digest = manifest.entries[0].blob_sha256
        os.utime(self.store.blob_path(digest), (0, 0))
        limited = ContentAddressedSnapshotStore(
            self.root / "store", gc_max_entries=2
        )

        with self.assertRaises(WorkspaceScanLimitError):
            limited.delete_orphans({"f" * 64}, older_than=time.time())

        self.assertTrue(limited.blob_path(digest).exists())


class GarbageCollectionTopologyTests(ReviewTestCase):
    def test_digest_in_the_wrong_valid_shard_fails_closed(self) -> None:
        manifest = self.store.put(snapshot(("file", b"orphan")), {"file": 0o600})
        digest = manifest.entries[0].blob_sha256
        correct = self.store.blob_path(digest)
        wrong_name = "ff" if digest[:2] != "ff" else "ee"
        wrong = correct.parent.parent / wrong_name / digest
        wrong.parent.mkdir()
        correct.replace(wrong)
        os.utime(wrong, (0, 0))

        with self.assertRaisesRegex(SnapshotIntegrityError, "shard"):
            self.store.delete_orphans(set(), older_than=time.time())

        self.assertTrue(wrong.exists())


@unittest.skipUnless(os.name == "posix", "POSIX directory-fd race semantics")
class PosixShardRaceTests(ReviewTestCase):
    def test_publish_does_not_follow_shard_swapped_after_validation(self) -> None:
        content = b"outside-write"
        digest = hashlib.sha256(content).hexdigest()
        shard = self.store.blob_path(digest).parent
        shard.mkdir(parents=True)
        external = self.root / "external"
        external.mkdir()
        real_lstat = Path.lstat
        swapped = False

        def swap_after_lstat(path: Path):
            nonlocal swapped
            metadata = real_lstat(path)
            if path == shard and not swapped:
                swapped = True
                shard.rename(shard.with_name(f"{shard.name}-owned"))
                shard.symlink_to(external, target_is_directory=True)
            return metadata

        with patch.object(Path, "lstat", new=swap_after_lstat):
            with self.assertRaises(SnapshotIntegrityError):
                self.store.put(snapshot(("file", content)), {"file": 0o600})

        self.assertFalse((external / digest).exists())

    def test_gc_does_not_follow_shard_swapped_after_validation(self) -> None:
        digest = hashlib.sha256(b"outside-delete").hexdigest()
        shard = self.store.blob_path(digest).parent
        shard.mkdir(parents=True)
        external = self.root / "external"
        external.mkdir()
        outside = external / digest
        outside.write_bytes(b"outside-delete")
        os.utime(outside, (0, 0))
        real_lstat = Path.lstat
        swapped = False

        def swap_after_lstat(path: Path):
            nonlocal swapped
            metadata = real_lstat(path)
            if path == shard and not swapped:
                swapped = True
                shard.rename(shard.with_name(f"{shard.name}-owned"))
                shard.symlink_to(external, target_is_directory=True)
            return metadata

        with patch.object(Path, "lstat", new=swap_after_lstat):
            with self.assertRaises(SnapshotIntegrityError):
                self.store.delete_orphans(set(), older_than=time.time())

        self.assertTrue(outside.exists())


@unittest.skipUnless(os.name == "nt", "Windows reparse identity checks")
class WindowsShardRaceTests(ReviewTestCase):
    def reparse_after_first_shard_check(self, shard: Path):
        real_lstat = Path.lstat
        checks = 0

        def changing_lstat(path: Path):
            nonlocal checks
            metadata = real_lstat(path)
            if path != shard:
                return metadata
            checks += 1
            if checks == 1:
                return metadata
            values = {
                name: getattr(metadata, name)
                for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
            }
            return SimpleNamespace(**values, st_file_attributes=0x400)

        return changing_lstat

    def test_publish_rechecks_shard_identity_before_side_effect(self) -> None:
        content = b"blocked"
        digest = hashlib.sha256(content).hexdigest()
        shard = self.store.blob_path(digest).parent
        shard.mkdir(parents=True)

        with patch.object(Path, "lstat", new=self.reparse_after_first_shard_check(shard)):
            with self.assertRaises(SnapshotIntegrityError):
                self.store.put(snapshot(("file", content)), {"file": 0o600})

        self.assertFalse(self.store.blob_path(digest).exists())

    def test_gc_rechecks_shard_identity_before_unlink(self) -> None:
        manifest = self.store.put(snapshot(("file", b"old")), {"file": 0o600})
        digest = manifest.entries[0].blob_sha256
        blob = self.store.blob_path(digest)
        os.utime(blob, (0, 0))

        with patch.object(Path, "lstat", new=self.reparse_after_first_shard_check(blob.parent)):
            with self.assertRaises(SnapshotIntegrityError):
                self.store.delete_orphans(set(), older_than=time.time())

        self.assertTrue(blob.exists())


if __name__ == "__main__":
    unittest.main()
