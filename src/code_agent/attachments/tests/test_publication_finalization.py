from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.attachments.errors import (  # noqa: E402
    AttachmentCommittedError,
    AttachmentError,
)
from code_agent.attachments.store import AttachmentStore  # noqa: E402


class AttachmentPublicationFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "attachments"
        self.payload = b"attachment finalization payload"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows committed publication")
    def test_close_failure_after_rename_reports_committed_reference(self) -> None:
        from code_agent import _windows_owned_temporary

        store = AttachmentStore(self.root)
        real_rename = _windows_owned_temporary._rename_relative
        real_close = _windows_owned_temporary._close
        renamed = False
        failed = False

        def rename(*args: object) -> None:
            nonlocal renamed
            real_rename(*args)  # type: ignore[arg-type]
            renamed = True

        def close(handle: int) -> None:
            nonlocal failed
            real_close(handle)
            if renamed and not failed:
                failed = True
                raise ctypes.WinError(5)

        with patch.object(
            _windows_owned_temporary, "_rename_relative", side_effect=rename
        ), patch.object(
            _windows_owned_temporary, "_close", side_effect=close
        ), self.assertRaises(AttachmentError) as raised:
            store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )

        digest = hashlib.sha256(self.payload).hexdigest()
        self.assertIsInstance(raised.exception, AttachmentCommittedError)
        self.assertEqual(raised.exception.reference.sha256, digest)
        self.assertEqual(
            (self.root / digest[:2] / f"{digest}.blob").read_bytes(),
            self.payload,
        )

    @unittest.skipUnless(os.name == "nt", "Windows guard ownership")
    def test_guard_close_failure_keeps_ownership_for_final_cleanup(self) -> None:
        from code_agent import _windows_owned_temporary

        store = AttachmentStore(self.root)
        real_hash = _windows_owned_temporary._sha256_handle
        real_close = _windows_owned_temporary._close
        fail_next_close = False
        failed_handle: int | None = None
        attempts: dict[int, int] = {}

        def hash_handle(handle: int) -> bytes:
            nonlocal fail_next_close
            result = real_hash(handle)
            fail_next_close = True
            return result

        def close(handle: int) -> None:
            nonlocal fail_next_close, failed_handle
            attempts[handle] = attempts.get(handle, 0) + 1
            if fail_next_close:
                fail_next_close = False
                failed_handle = handle
                raise ctypes.WinError(5)
            real_close(handle)

        try:
            with patch.object(
                _windows_owned_temporary,
                "_sha256_handle",
                side_effect=hash_handle,
            ), patch.object(
                _windows_owned_temporary, "_close", side_effect=close
            ), self.assertRaises(AttachmentError):
                store.put(
                    self.payload,
                    media_type="text/plain",
                    display_name="note.txt",
                )
            assert failed_handle is not None
            self.assertGreaterEqual(attempts[failed_handle], 2)
        finally:
            if failed_handle is not None and attempts.get(failed_handle) == 1:
                real_close(failed_handle)

    def test_cleanup_lock_after_concurrent_commit_is_reported(self) -> None:
        store = AttachmentStore(self.root)

        def publish_winner(
            _owned: object, destination: Path, _verifier: object
        ) -> None:
            destination.write_bytes(self.payload)
            raise FileExistsError(17, "concurrent winner")

        with patch(
            "code_agent.attachments.store.OwnedTemporary.publish_no_replace",
            autospec=True,
            side_effect=publish_winner,
        ), patch(
            "code_agent.attachments.store.OwnedTemporary.cleanup",
            autospec=True,
            side_effect=PermissionError("pending file is locked"),
        ), self.assertRaises(AttachmentCommittedError) as raised:
            store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )

        self.assertIn("cleanup", str(raised.exception))

    def test_identity_capture_failure_cleans_pending_file(self) -> None:
        store = AttachmentStore(self.root)

        with patch(
            "code_agent.attachments.store.OwnedTemporary.capture_descriptor",
            side_effect=OSError("identity unavailable"),
        ), self.assertRaises(AttachmentError):
            store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )

        self.assertEqual(tuple(self.root.rglob(".pending-*")), ())


if __name__ == "__main__":
    unittest.main()
