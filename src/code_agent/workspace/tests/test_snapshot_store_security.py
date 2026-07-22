from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
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
)
from code_agent.workspace import snapshot_store as store_module  # noqa: E402
from code_agent.workspace import _posix_io  # noqa: E402


def replace_target():
    if os.name == "posix":
        return patch.object(_posix_io, "replace")
    return patch("code_agent.workspace.snapshot_store.os.replace")


class SnapshotStoreSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def store(self, **limits: object) -> ContentAddressedSnapshotStore:
        return ContentAddressedSnapshotStore(self.root / "store", **limits)

    def put(self, store: ContentAddressedSnapshotStore, name: str = "a.py", data: bytes = b"ok"):
        return store.put(WorkspaceSnapshot((SnapshotEntry(name, data, True),)), {name: 0o644})

    def test_publish_uses_fsynced_same_shard_temp_then_replace(self) -> None:
        store = self.store()
        real_replace = os.replace
        replaced: list[tuple[Path, Path]] = []
        fsync_calls: list[int] = []

        def record_replace(*arguments: object) -> None:
            if os.name == "posix":
                parent_fd, source, target = arguments
                replaced.append((Path(str(source)), Path(str(target))))
                real_replace(
                    source,
                    target,
                    src_dir_fd=int(parent_fd),
                    dst_dir_fd=int(parent_fd),
                )
            else:
                source, target = arguments
                replaced.append((Path(source), Path(target)))
                real_replace(source, target)

        with patch("code_agent.workspace.snapshot_store.os.fsync", side_effect=lambda fd: fsync_calls.append(fd)):
            with replace_target() as mocked_replace:
                mocked_replace.side_effect = record_replace
                manifest = self.put(store)

        self.assertTrue(fsync_calls)
        self.assertEqual(len(replaced), 1)
        source, target = replaced[0]
        self.assertEqual(source.parent, target.parent)
        self.assertTrue(source.name.startswith(".tmp-"))
        if os.name == "posix":
            self.assertEqual(target.name, manifest.entries[0].blob_sha256)
        else:
            self.assertEqual(target, store.blob_path(manifest.entries[0].blob_sha256))

    def test_publish_failure_cleans_owned_temp(self) -> None:
        store = self.store()

        with replace_target() as mocked_replace:
            mocked_replace.side_effect = PermissionError("locked")
            with self.assertRaisesRegex(OSError, "locked"):
                self.put(store)

        self.assertEqual(tuple((self.root / "store").rglob(".tmp-*")), ())

    def test_concurrent_valid_blob_after_replace_failure_is_success(self) -> None:
        store = self.store()
        content = b"race"
        digest = hashlib.sha256(content).hexdigest()

        def publish_other(*arguments: object) -> None:
            if os.name == "posix":
                parent_fd, _source, target = arguments
                descriptor = os.open(
                    str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                    dir_fd=int(parent_fd),
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
            else:
                _source, target = arguments
                Path(target).write_bytes(content)
            raise PermissionError("destination appeared")

        with replace_target() as mocked_replace:
            mocked_replace.side_effect = publish_other
            manifest = self.put(store, data=content)

        self.assertEqual(manifest.entries[0].blob_sha256, digest)
        self.assertEqual(tuple((self.root / "store").rglob(".tmp-*")), ())

    def test_existing_corrupt_blob_is_never_silently_replaced(self) -> None:
        store = self.store()
        digest = hashlib.sha256(b"right").hexdigest()
        path = store.blob_path(digest)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"wrong")

        with self.assertRaisesRegex(SnapshotIntegrityError, "existing blob"):
            self.put(store, data=b"right")
        self.assertEqual(path.read_bytes(), b"wrong")

    def test_materialize_rejects_a_blob_symlink(self) -> None:
        store = self.store()
        manifest = self.put(store)
        blob = store.blob_path(manifest.entries[0].blob_sha256)
        external = self.root / "external"
        external.write_bytes(b"ok")
        blob.unlink()
        try:
            blob.symlink_to(external)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")

        with self.assertRaisesRegex(SnapshotIntegrityError, "regular blob"):
            store.materialize(manifest)


class SnapshotStoreGarbageCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = ContentAddressedSnapshotStore(self.root / "store")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def blob(self, content: bytes, age_s: float = 100.0) -> str:
        manifest = self.store.put(
            WorkspaceSnapshot((SnapshotEntry("file", content, True),)),
            {"file": 0o600},
        )
        digest = manifest.entries[0].blob_sha256
        os.utime(self.store.blob_path(digest), (time.time() - age_s,) * 2)
        return digest

    def test_deletes_only_old_unreferenced_blobs(self) -> None:
        referenced = self.blob(b"referenced")
        orphan = self.blob(b"orphan")
        newer = self.blob(b"newer", age_s=0)

        deleted = self.store.delete_orphans({referenced}, older_than=time.time() - 10)

        self.assertEqual(deleted, (orphan,))
        self.assertTrue(self.store.blob_path(referenced).exists())
        self.assertTrue(self.store.blob_path(newer).exists())

    def test_temp_and_nonblob_entries_are_never_deleted(self) -> None:
        digest = self.blob(b"orphan")
        shard = self.store.blob_path(digest).parent
        temp = shard / ".tmp-owned"
        nonblob = shard / "README"
        temp.write_bytes(b"temp")
        nonblob.write_bytes(b"metadata")
        os.utime(temp, (0, 0))
        os.utime(nonblob, (0, 0))

        self.store.delete_orphans(set(), older_than=time.time())

        self.assertTrue(temp.exists())
        self.assertTrue(nonblob.exists())

    def test_invalid_reference_is_rejected_before_scan(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.store.delete_orphans({"../outside"}, older_than=time.time())

    def test_entry_limit_fails_before_deleting_any_blob(self) -> None:
        digests = (self.blob(b"one"), self.blob(b"two"))
        limited = ContentAddressedSnapshotStore(self.root / "store", gc_max_entries=1)

        with self.assertRaises(WorkspaceScanLimitError):
            limited.delete_orphans(set(), older_than=time.time())

        self.assertTrue(all(limited.blob_path(digest).exists() for digest in digests))

    def test_deadline_fails_before_deleting_any_blob(self) -> None:
        digest = self.blob(b"old")
        limited = ContentAddressedSnapshotStore(self.root / "store", gc_deadline_s=1.0)

        with patch("code_agent.workspace.snapshot_store.time.monotonic", side_effect=(0.0, 2.0)):
            with self.assertRaises(SearchTimeoutError):
                limited.delete_orphans(set(), older_than=time.time())

        self.assertTrue(limited.blob_path(digest).exists())

    def test_deadline_is_rechecked_before_deletion(self) -> None:
        digest = self.blob(b"old")
        limited = ContentAddressedSnapshotStore(self.root / "store", gc_deadline_s=1.0)

        with patch(
            "code_agent.workspace.snapshot_store.time.monotonic",
            side_effect=(0.0, 0.0, 0.0, 0.0, 2.0),
        ):
            with self.assertRaises(SearchTimeoutError):
                limited.delete_orphans(set(), older_than=time.time())

        self.assertTrue(limited.blob_path(digest).exists())

    def test_matching_symlink_is_rejected_and_preserved(self) -> None:
        digest = self.blob(b"old")
        blob = self.store.blob_path(digest)
        target = self.root / "outside"
        target.write_bytes(b"outside")
        blob.unlink()
        try:
            blob.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")

        with self.assertRaisesRegex(SnapshotIntegrityError, "regular blob"):
            self.store.delete_orphans(set(), older_than=time.time())
        self.assertTrue(blob.is_symlink())

    def test_broken_blob_root_symlink_is_rejected(self) -> None:
        blobs = self.root / "store" / "blobs"
        blobs.parent.mkdir(parents=True)
        try:
            blobs.symlink_to(self.root / "missing", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")

        with self.assertRaisesRegex(SnapshotIntegrityError, "directory"):
            self.store.delete_orphans(set(), older_than=time.time())

    def test_candidate_replaced_by_broken_symlink_is_rejected(self) -> None:
        digest = self.blob(b"old")
        blob = self.store.blob_path(digest)
        real_collect = store_module.collect_orphans

        def collect_then_replace(*args: object, **kwargs: object):
            candidates = real_collect(*args, **kwargs)
            blob.unlink()
            blob.symlink_to(self.root / "missing")
            return candidates

        try:
            with patch.object(store_module, "collect_orphans", side_effect=collect_then_replace):
                with self.assertRaisesRegex(SnapshotIntegrityError, "regular blob"):
                    self.store.delete_orphans(set(), older_than=time.time())
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        self.assertTrue(blob.is_symlink())


if __name__ == "__main__":
    unittest.main()
