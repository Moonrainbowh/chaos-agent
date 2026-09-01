from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace._windows_file_locks import (  # noqa: E402
    DELETE_RETRY_WINERRORS,
    READ_RETRY_WINERRORS,
    REPLACE_RETRY_WINERRORS,
    retry_windows_file_operation,
)
from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import (  # noqa: E402
    EditConflictError,
    WindowsFileBusyError,
    WorkspaceError,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace._secure_temp import TEMP_PREFIX  # noqa: E402


def _windows_error(code: int, path: Path) -> OSError:
    return OSError(13, "Windows access failure", str(path), code)


@unittest.skipUnless(os.name == "nt", "Windows file-lock semantics")
class RetryPolicyTests(unittest.TestCase):
    def test_implicit_winerror_context_does_not_retry_program_errors(self) -> None:
        target = Path("locked.py")
        for error_type in (ValueError, WorkspaceError):
            with self.subTest(error_type=error_type.__name__):
                attempts = 0

                def action() -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts > 1:
                        return
                    try:
                        raise _windows_error(32, target)
                    except OSError:
                        raise error_type("program failure")

                with self.assertRaises(error_type):
                    retry_windows_file_operation(
                        action,
                        target=target,
                        operation="read",
                        timeout_s=0.1,
                        retry_winerrors=READ_RETRY_WINERRORS,
                    )
                self.assertEqual(attempts, 1)

    def test_explicit_workspace_error_cause_retries_native_winerror(self) -> None:
        target = Path("locked.py")
        attempts = 0

        def action() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                return "done"
            try:
                raise _windows_error(33, target)
            except OSError as error:
                raise WorkspaceError("guarded read failed") from error

        result = retry_windows_file_operation(
            action,
            target=target,
            operation="read",
            timeout_s=0.1,
            retry_winerrors=READ_RETRY_WINERRORS,
        )

        self.assertEqual(result, "done")
        self.assertEqual(attempts, 2)

    def test_replace_retries_ambiguous_access_denied_but_delete_does_not(self) -> None:
        target = Path("locked.py")
        now = [0.0]
        attempts = 0
        validations = 0

        def action() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _windows_error(5, target)
            return "done"

        def validate() -> None:
            nonlocal validations
            validations += 1

        result = retry_windows_file_operation(
            action,
            target=target,
            operation="replace",
            timeout_s=0.1,
            retry_winerrors=REPLACE_RETRY_WINERRORS,
            validate=validate,
            clock=lambda: now[0],
            sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
        )

        self.assertEqual(result, "done")
        self.assertEqual((attempts, validations), (3, 3))

        with self.assertRaises(PermissionError):
            retry_windows_file_operation(
                lambda: (_ for _ in ()).throw(_windows_error(5, target)),
                target=target,
                operation="delete",
                timeout_s=0.1,
                retry_winerrors=DELETE_RETRY_WINERRORS,
            )

    def test_committed_publication_error_is_never_retried(self) -> None:
        target = Path("published.py")
        attempts = 0
        native = _windows_error(5, target)
        committed = WorkspaceError("post-publication recovery failed")
        committed.__cause__ = native
        committed.publication_committed = True  # type: ignore[attr-defined]

        def action() -> None:
            nonlocal attempts
            attempts += 1
            raise committed

        with self.assertRaises(WorkspaceError) as raised:
            retry_windows_file_operation(
                action,
                target=target,
                operation="replace",
                timeout_s=0.1,
                retry_winerrors=REPLACE_RETRY_WINERRORS,
            )

        self.assertIs(raised.exception, committed)
        self.assertEqual(attempts, 1)

    def test_timeout_is_bounded_actionable_and_preserves_winerror(self) -> None:
        target = Path("locked.py")
        now = [0.0]

        with self.assertRaises(WindowsFileBusyError) as raised:
            retry_windows_file_operation(
                lambda: (_ for _ in ()).throw(_windows_error(5, target)),
                target=target,
                operation="replace",
                timeout_s=0.025,
                retry_winerrors=REPLACE_RETRY_WINERRORS,
                clock=lambda: now[0],
                sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
            )

        self.assertIn(raised.exception.winerror, {5, 32})
        self.assertEqual(raised.exception.path, target)
        self.assertIn("may be in use", str(raised.exception))
        self.assertIn("permissions or attributes", str(raised.exception))
        self.assertLessEqual(now[0], 0.025)


@unittest.skipUnless(os.name == "nt", "Windows file-lock semantics")
class RealWindowsWorkspaceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(
            WorkspacePathGuard(self.root), file_lock_timeout_s=0.3
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_locked_edit_retries_until_handle_is_released(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before\n")
        plan = self.editor.plan_write("module.py", "after\n")
        handle = _open_without_delete_share(target)
        worker = threading.Thread(
            target=_release_after, args=(handle, 0.05), daemon=True
        )
        worker.start()

        self.editor.apply(plan)
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(target.read_bytes(), b"after\n")
        self.assertEqual(list(self.root.glob(f"{TEMP_PREFIX}*")), [])

    def test_locked_edit_timeout_keeps_target_and_cleans_temp(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before\n")
        plan = self.editor.plan_write("module.py", "after\n")
        editor = WorkspaceEditor(
            WorkspacePathGuard(self.root), file_lock_timeout_s=0.04
        )
        handle = _open_without_delete_share(target)
        try:
            with self.assertRaises(WindowsFileBusyError) as raised:
                editor.apply(plan)
        finally:
            windows_open._close_handle(handle)

        self.assertIn(raised.exception.winerror, {5, 32})
        self.assertEqual(target.read_bytes(), b"before\n")
        self.assertEqual(list(self.root.glob(f"{TEMP_PREFIX}*")), [])

    def test_edit_revalidates_content_after_waiting_for_lock(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before\n")
        plan = self.editor.plan_write("module.py", "agent\n")
        stream = target.open("r+b")
        failures: list[BaseException] = []

        def mutate_then_release() -> None:
            try:
                time.sleep(0.05)
                stream.seek(0)
                stream.truncate()
                stream.write(b"concurrent\n")
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException as error:
                failures.append(error)
            finally:
                stream.close()

        worker = threading.Thread(target=mutate_then_release, daemon=True)
        worker.start()
        with self.assertRaises(EditConflictError):
            self.editor.apply(plan)
        worker.join(1)

        self.assertEqual(failures, [])
        self.assertEqual(target.read_bytes(), b"concurrent\n")
        self.assertEqual(list(self.root.glob(f"{TEMP_PREFIX}*")), [])

    def test_restore_delete_retries_winerror_32(self) -> None:
        target = self.root / "created-after-checkpoint.txt"
        target.write_bytes(b"remove me")
        snapshot = WorkspaceSnapshot(
            (SnapshotEntry(target.name, None, False),)
        )
        handle = _open_without_delete_share(target)
        worker = threading.Thread(
            target=_release_after, args=(handle, 0.05), daemon=True
        )
        worker.start()

        self.editor.restore(snapshot)
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertFalse(target.exists())


def _open_without_delete_share(path: Path) -> int:
    return windows_open._create_handle(
        path,
        desired_access=0x80000000,
        share_mode=0x1 | 0x2,
        flags=windows_open._OPEN_REPARSE_POINT,
    )


def _release_after(handle: int, delay: float) -> None:
    time.sleep(delay)
    windows_open._close_handle(handle)


if __name__ == "__main__":
    unittest.main()
