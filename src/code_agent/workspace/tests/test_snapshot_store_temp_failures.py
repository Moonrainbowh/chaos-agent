from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _posix_io  # noqa: E402
from code_agent.workspace import _snapshot_blob_io as blob_io  # noqa: E402
from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.snapshot_store import (  # noqa: E402
    ContentAddressedSnapshotStore,
)


class TempCreationFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = ContentAddressedSnapshotStore(self.root / "store")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def put(self) -> None:
        self.store.put(
            WorkspaceSnapshot((SnapshotEntry("file", b"content", True),)),
            {"file": 0o600},
        )

    def temp_paths(self) -> tuple[Path, ...]:
        return tuple(self.store.blobs_root.rglob(".tmp-*"))

    def assert_stream_failure_cleans(self, stage: str) -> None:
        primary = OSError(f"{stage} failed")

        def fail_at_stage(stream: object, content: bytes, fsync: object) -> None:
            del fsync
            if stage == "write":
                raise primary
            stream.write(content)
            if stage == "flush":
                raise primary
            stream.flush()
            raise primary

        with patch.object(blob_io, "_write_stream", side_effect=fail_at_stage):
            with self.assertRaises(OSError) as raised:
                self.put()

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(raised.exception), f"{stage} failed")
        self.assertEqual(self.temp_paths(), ())

    def test_write_failure_cleans_owned_temp(self) -> None:
        self.assert_stream_failure_cleans("write")

    def test_flush_failure_cleans_owned_temp(self) -> None:
        self.assert_stream_failure_cleans("flush")

    def test_fsync_failure_cleans_owned_temp(self) -> None:
        primary = OSError("fsync failed")

        with patch(
            "code_agent.workspace.snapshot_store.os.fsync", side_effect=primary
        ):
            with self.assertRaises(OSError) as raised:
                self.put()

        self.assertIs(raised.exception, primary)
        self.assertEqual(self.temp_paths(), ())

    def test_post_write_parent_verification_failure_cleans_temp(self) -> None:
        primary = WorkspaceError("verify chain failed")
        real_verify = blob_io.verify_chain
        calls = 0

        def fail_second(root: object, shard: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise primary
            real_verify(root, shard)

        with patch.object(blob_io, "verify_chain", side_effect=fail_second):
            with self.assertRaises(WorkspaceError) as raised:
                self.put()

        self.assertIs(raised.exception, primary)
        self.assertEqual(self.temp_paths(), ())

    def test_visible_identity_failure_cleans_owned_temp(self) -> None:
        primary = OSError("visible identity failed")
        real_inspect = blob_io.inspect_regular
        failed = False

        def fail_first_temp(*args: object, **kwargs: object):
            nonlocal failed
            label = args[2]
            if label == "temporary blob" and not failed:
                failed = True
                raise primary
            return real_inspect(*args, **kwargs)

        with patch.object(blob_io, "inspect_regular", side_effect=fail_first_temp):
            with self.assertRaises(OSError) as raised:
                self.put()

        self.assertIs(raised.exception, primary)
        self.assertEqual(self.temp_paths(), ())

    def test_cleanup_failure_keeps_primary_and_attaches_secondary(self) -> None:
        primary = OSError("write failed")
        cleanup = OSError("unlink failed")
        unlink_target = _posix_io if os.name == "posix" else Path

        with patch.object(blob_io, "_write_stream", side_effect=primary):
            with patch.object(unlink_target, "unlink", side_effect=cleanup):
                with self.assertRaises(OSError) as raised:
                    self.put()

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(raised.exception), "write failed")
        cleanup_error = getattr(primary, "cleanup_error", None)
        self.assertIsInstance(cleanup_error, WorkspaceError)
        self.assertRegex(str(cleanup_error), "clean temporary blob")
        self.assertEqual(len(self.temp_paths()), 1)

    def test_unproven_temp_identity_is_not_deleted(self) -> None:
        primary = OSError("write failed")
        real_inspect = blob_io.inspect_regular

        def substitute_identity(*args: object, **kwargs: object):
            identity = real_inspect(*args, **kwargs)
            if args[2] == "temporary blob" and identity is not None:
                return replace(identity, inode=identity.inode + 1)
            return identity

        with patch.object(blob_io, "_write_stream", side_effect=primary):
            with patch.object(
                blob_io, "inspect_regular", side_effect=substitute_identity
            ):
                with self.assertRaises(OSError) as raised:
                    self.put()

        self.assertIs(raised.exception, primary)
        self.assertRegex(str(primary.cleanup_error), "ownership failure")
        self.assertEqual(len(self.temp_paths()), 1)


if __name__ == "__main__":
    unittest.main()
