from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.attachments.errors import (  # noqa: E402
    AttachmentError,
    AttachmentIntegrityError,
)
from code_agent.attachments.store import AttachmentStore  # noqa: E402


class _OrderedPublishStore(AttachmentStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._publishers_ready = threading.Barrier(2)
        self._first_published = threading.Event()
        self._order_lock = threading.Lock()
        self._next_order = 0
        self.first_file_id: int | None = None

    def _write_atomic(
        self, directory: Path, destination: Path, data: bytes
    ) -> object:
        self._publishers_ready.wait(timeout=5)
        with self._order_lock:
            order = self._next_order
            self._next_order += 1
        if order == 0:
            result = super()._write_atomic(directory, destination, data)
            self.first_file_id = destination.stat().st_ino
            self._first_published.set()
            return result
        if not self._first_published.wait(timeout=5):
            raise TimeoutError("first attachment publisher did not finish")
        return super()._write_atomic(directory, destination, data)


class _PausedPublishStore(AttachmentStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.publish_ready = threading.Event()
        self.release_publish = threading.Event()

    def _write_atomic(
        self, directory: Path, destination: Path, data: bytes
    ) -> object:
        self.publish_ready.set()
        if not self.release_publish.wait(timeout=5):
            raise TimeoutError("attachment publish was not released")
        return super()._write_atomic(directory, destination, data)


class _LockAfterPublishStore(AttachmentStore):
    def __init__(self, root: Path, *, release_after: float | None) -> None:
        super().__init__(root)
        self._release_after = release_after
        self._handle: int | None = None
        self._timer: threading.Timer | None = None

    def _write_atomic(
        self, directory: Path, destination: Path, data: bytes
    ) -> object:
        result = super()._write_atomic(directory, destination, data)
        self._handle = _open_exclusive(destination)
        if self._release_after is not None:
            self._timer = threading.Timer(self._release_after, self.release)
            self._timer.start()
        return result

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            _close_handle(handle)
        timer, self._timer = self._timer, None
        if timer is not None and timer is not threading.current_thread():
            timer.join(timeout=1)


class AttachmentStoreConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "attachments"
        self.payload = b"same normalized attachment payload"

    def test_same_digest_publish_is_idempotent_without_replacement(self) -> None:
        store = _OrderedPublishStore(self.root)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    store.put,
                    self.payload,
                    media_type="text/plain",
                    display_name="note.txt",
                )
                for _ in range(2)
            ]
            references = [future.result(timeout=10) for future in futures]

        destination = self._destination(references[0].sha256)
        self.assertEqual(references[0], references[1])
        self.assertEqual(store.read(references[0]), self.payload)
        self.assertEqual(store.first_file_id, destination.stat().st_ino)
        self.assertEqual(tuple(self.root.rglob(".pending-*")), ())

    def test_conflicting_race_fails_without_overwrite_or_pending_file(self) -> None:
        store = _PausedPublishStore(self.root)
        digest = hashlib.sha256(self.payload).hexdigest()
        destination = self._destination(digest)
        conflicting = b"conflicting blob"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                store.put,
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )
            self.assertTrue(store.publish_ready.wait(timeout=5))
            destination.write_bytes(conflicting)
            store.release_publish.set()
            with self.assertRaises(AttachmentIntegrityError):
                future.result(timeout=10)

        self.assertEqual(destination.read_bytes(), conflicting)
        self.assertEqual(tuple(self.root.rglob(".pending-*")), ())

    @unittest.skipUnless(os.name == "nt", "Windows publication contract")
    def test_windows_publish_does_not_require_hard_link_support(self) -> None:
        store = AttachmentStore(self.root)

        with patch(
            "code_agent.attachments.store.os.link",
            side_effect=OSError(50, "hard links are not supported"),
        ):
            reference = store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )

        self.assertEqual(store.read(reference), self.payload)
        self.assertEqual(tuple(self.root.rglob(".pending-*")), ())

    @unittest.skipUnless(os.name == "nt", "Windows publication contract")
    def test_committed_blob_retries_a_transient_read_lock(self) -> None:
        store = _LockAfterPublishStore(self.root, release_after=0.05)
        try:
            reference = store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )
        finally:
            store.release()

        self.assertEqual(store.read(reference), self.payload)

    @unittest.skipUnless(os.name == "nt", "Windows publication contract")
    def test_committed_blob_timeout_reports_reference_and_truth(self) -> None:
        store = _LockAfterPublishStore(self.root, release_after=None)
        try:
            with self.assertRaises(AttachmentError) as raised:
                store.put(
                    self.payload,
                    media_type="text/plain",
                    display_name="note.txt",
                )
            self.assertTrue(getattr(raised.exception, "committed", False))
            self.assertEqual(
                getattr(raised.exception, "reference", None).sha256,
                hashlib.sha256(self.payload).hexdigest(),
            )
        finally:
            store.release()

        self.assertEqual(self._destination_hash().read_bytes(), self.payload)

    def test_cleanup_preserves_and_reports_foreign_pending_replacement(self) -> None:
        store = AttachmentStore(self.root)
        replacements: list[Path] = []

        def publish_then_replace_temporary(
            owned: object,
            destination: Path,
            verifier: object,
        ) -> None:
            del verifier
            temporary = getattr(owned, "path")
            temporary.unlink()
            temporary.write_bytes(b"belongs to another process")
            replacements.append(temporary)
            Path(destination).write_bytes(self.payload)
            raise FileExistsError(17, "injected concurrent winner")

        with patch(
            "code_agent.attachments.store.OwnedTemporary.publish_no_replace",
            autospec=True,
            side_effect=publish_then_replace_temporary,
        ), self.assertRaises(AttachmentError) as raised:
            store.put(
                self.payload,
                media_type="text/plain",
                display_name="note.txt",
            )

        self.assertTrue(getattr(raised.exception, "committed", False))
        self.assertIn("replaced", str(raised.exception))
        self.assertEqual(
            replacements[0].read_bytes(),
            b"belongs to another process",
        )

    def _destination(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.blob"

    def _destination_hash(self) -> Path:
        return self._destination(hashlib.sha256(self.payload).hexdigest())


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CREATE_FILE = _KERNEL32.CreateFileW
    _CREATE_FILE.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    _CREATE_FILE.restype = ctypes.c_void_p
    _CLOSE_HANDLE = _KERNEL32.CloseHandle
    _CLOSE_HANDLE.argtypes = [ctypes.c_void_p]
    _CLOSE_HANDLE.restype = ctypes.c_int


def _open_exclusive(path: Path) -> int:
    handle = _CREATE_FILE(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _close_handle(handle: int) -> None:
    if not _CLOSE_HANDLE(handle):
        raise ctypes.WinError(ctypes.get_last_error())


if __name__ == "__main__":
    unittest.main()
