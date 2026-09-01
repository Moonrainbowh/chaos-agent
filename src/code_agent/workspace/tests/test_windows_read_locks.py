from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace._windows_file_locks import (  # noqa: E402
    READ_RETRY_WINERRORS,
    retry_windows_file_operation,
)
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.errors import WindowsFileBusyError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class _Overlapped(ctypes.Structure):
    _fields_ = (
        ("internal", ctypes.c_void_p),
        ("internal_high", ctypes.c_void_p),
        ("offset", wintypes.DWORD),
        ("offset_high", wintypes.DWORD),
        ("event", wintypes.HANDLE),
    )


def _windows_error(code: int, path: Path) -> OSError:
    return OSError(13, "Windows access failure", str(path), code)


@unittest.skipUnless(os.name == "nt", "Windows file-lock semantics")
class WindowsReadLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_write_retries_until_byte_range_lock_is_released(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before\n")
        editor = WorkspaceEditor(
            WorkspacePathGuard(self.root), file_lock_timeout_s=0.3
        )
        handle, overlapped = _lock_first_byte(target)
        worker = threading.Thread(
            target=_unlock_after,
            args=(handle, overlapped, 0.05),
            daemon=True,
        )
        worker.start()
        try:
            plan = editor.plan_write("module.py", "after\n")
        finally:
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(plan.existed)
        self.assertIn("-before", plan.diff)

    def test_plan_write_reports_persistent_byte_range_lock(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before\n")
        editor = WorkspaceEditor(
            WorkspacePathGuard(self.root), file_lock_timeout_s=0.04
        )
        handle, overlapped = _lock_first_byte(target)
        try:
            with self.assertRaises(WindowsFileBusyError) as raised:
                editor.plan_write("module.py", "after\n")
        finally:
            _unlock_and_close(handle, overlapped)

        self.assertEqual(raised.exception.winerror, 33)
        self.assertEqual(raised.exception.path, target)
        self.assertIn("locked", str(raised.exception))

    def test_read_does_not_reclassify_access_denied_as_lock(self) -> None:
        target = self.root / "denied.py"
        attempts = 0

        def deny() -> None:
            nonlocal attempts
            attempts += 1
            raise _windows_error(5, target)

        with self.assertRaises(PermissionError) as raised:
            retry_windows_file_operation(
                deny,
                target=target,
                operation="read file",
                timeout_s=0.1,
                retry_winerrors=READ_RETRY_WINERRORS,
            )

        self.assertNotIsInstance(raised.exception, WindowsFileBusyError)
        self.assertEqual(raised.exception.winerror, 5)
        self.assertEqual(attempts, 1)


def _lock_first_byte(path: Path) -> tuple[int, _Overlapped]:
    handle = windows_open._create_handle(
        path,
        desired_access=0x80000000,
        share_mode=0x1 | 0x2 | 0x4,
        flags=windows_open._OPEN_REPARSE_POINT,
    )
    overlapped = _Overlapped()
    function = ctypes.WinDLL("kernel32", use_last_error=True).LockFileEx
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    function.restype = wintypes.BOOL
    if function(handle, 0x1 | 0x2, 0, 1, 0, ctypes.byref(overlapped)):
        return handle, overlapped
    error = ctypes.WinError(ctypes.get_last_error())
    windows_open._close_handle(handle)
    raise error


def _unlock_after(handle: int, overlapped: _Overlapped, delay: float) -> None:
    time.sleep(delay)
    _unlock_and_close(handle, overlapped)


def _unlock_and_close(handle: int, overlapped: _Overlapped) -> None:
    function = ctypes.WinDLL("kernel32", use_last_error=True).UnlockFileEx
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    function.restype = wintypes.BOOL
    try:
        if not function(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        windows_open._close_handle(handle)


if __name__ == "__main__":
    unittest.main()
