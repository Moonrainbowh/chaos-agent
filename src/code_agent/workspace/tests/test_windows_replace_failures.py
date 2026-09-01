from __future__ import annotations

import ctypes
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_atomic_replace  # noqa: E402
from code_agent.workspace import _windows_replace_recovery  # noqa: E402
from code_agent.workspace._secure_temp import TEMP_PREFIX  # noqa: E402
from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows ReplaceFile failure semantics")
class WindowsReplaceFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        for path in self.root.glob(f"{TEMP_PREFIX}*"):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        self.temporary.cleanup()

    def test_1175_and_1176_preserve_original_and_clean_owned_files(self) -> None:
        for code in (1175, 1176):
            with self.subTest(winerror=code):
                target = self.root / f"module-{code}.py"
                target.write_bytes(b"before")
                snapshot = WorkspaceSnapshot(
                    (SnapshotEntry(target.name, b"after", True),)
                )

                with patch.object(
                    _windows_atomic_replace,
                    "_replace_file",
                    side_effect=ctypes.WinError(code),
                ):
                    with self.assertRaisesRegex(
                        WorkspaceError, "partially completed"
                    ):
                        self.editor.restore(snapshot)

                self.assertEqual(target.read_bytes(), b"before")
                self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())

    def test_1177_restores_owned_backup_when_target_is_missing(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry(target.name, b"after", True),))

        def partial_move(final: Path, temporary: Path, backup: Path) -> None:
            os.replace(final, backup)
            raise ctypes.WinError(1177)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=partial_move
        ):
            with self.assertRaisesRegex(WorkspaceError, "partially completed"):
                self.editor.restore(snapshot)

        self.assertEqual(target.read_bytes(), b"before")
        self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())

    def test_backup_name_is_absent_until_native_replace(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        plan = self.editor.plan_write(target.name, "after")
        real_replace = _windows_atomic_replace._replace_file
        observed: list[bool] = []

        def replace(final: Path, temporary: Path, backup: Path) -> None:
            observed.append(not backup.exists())
            real_replace(final, temporary, backup)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=replace
        ):
            self.editor.apply(plan)

        self.assertEqual(observed, [True])
        self.assertEqual(target.read_bytes(), b"after")
        self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())

    def test_backup_reservation_failure_does_not_leak_target_guard(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        plan = self.editor.plan_write(target.name, "after")
        real_open = _windows_atomic_replace._open_file_guard
        real_close = _windows_atomic_replace._close_handle
        opened: list[int] = []

        def capture_open(path: Path, access: int) -> int:
            handle = real_open(path, access)
            opened.append(handle)
            return handle

        with patch.object(
            _windows_atomic_replace,
            "_reserve_backup",
            side_effect=WorkspaceError("cannot reserve backup"),
        ), patch.object(
            _windows_atomic_replace,
            "_open_file_guard",
            side_effect=capture_open,
        ):
            with self.assertRaisesRegex(WorkspaceError, "reserve backup"):
                self.editor.apply(plan)

        blocked: OSError | None = None
        try:
            with target.open("r+b"):
                pass
        except OSError as error:
            blocked = error
        finally:
            if blocked is not None:
                for handle in reversed(opened):
                    try:
                        real_close(handle)
                    except OSError:
                        pass
        self.assertIsNone(blocked)

    def test_occupied_backup_name_is_preserved_before_replace(self) -> None:
        target = self.root / "module.py"
        foreign = self.root / "user-important.py"
        target.write_bytes(b"before")
        foreign.write_bytes(b"user-important")
        plan = self.editor.plan_write(target.name, "after")
        real_replace = _windows_atomic_replace._replace_file
        occupied: list[Path] = []

        def occupy_backup(final: Path, temporary: Path, backup: Path) -> None:
            os.replace(foreign, backup)
            occupied.append(backup)
            real_replace(final, temporary, backup)

        with patch.object(
            _windows_atomic_replace,
            "_replace_file",
            side_effect=occupy_backup,
        ):
            with self.assertRaises(WorkspaceError):
                self.editor.apply(plan)

        self.assertEqual(target.read_bytes(), b"before")
        self.assertEqual(len(occupied), 1)
        self.assertEqual(occupied[0].read_bytes(), b"user-important")

    def test_occupied_recovery_name_preserves_every_file(self) -> None:
        target = self.root / "module.py"
        away = self.root / "user-renamed.py"
        foreign = self.root / "user-important.py"
        target.write_bytes(b"before")
        foreign.write_bytes(b"user-important")
        plan = self.editor.plan_write(target.name, "agent")
        real_replace = _windows_atomic_replace._replace_file
        real_recovery = _windows_replace_recovery.replace_file

        def first_replace(final: Path, temporary: Path, backup: Path) -> None:
            os.rename(final, away)
            final.write_bytes(b"user-new")
            real_replace(final, temporary, backup)

        def occupy_recovery(
            final: Path, replacement: Path, recovery: Path
        ) -> None:
            os.replace(foreign, recovery)
            real_recovery(final, replacement, recovery)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=first_replace
        ), patch.object(
            _windows_replace_recovery,
            "replace_file",
            side_effect=occupy_recovery,
        ):
            with self.assertRaises(WorkspaceError):
                self.editor.apply(plan)

        contents = {path.read_bytes() for path in self.root.iterdir()}
        self.assertTrue(
            {b"before", b"agent", b"user-new", b"user-important"}
            <= contents
        )

    def test_committed_replace_survives_finalizer_error_without_retry(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        plan = self.editor.plan_write(target.name, "after")
        real_replace = _windows_atomic_replace._replace_file
        real_close = _windows_atomic_replace._close_handle
        replace_calls = 0
        close_calls = 0

        def replace(final: Path, temporary: Path, backup: Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            real_replace(final, temporary, backup)

        def close_then_fail(handle: int) -> None:
            nonlocal close_calls
            close_calls += 1
            real_close(handle)
            if close_calls == 3:
                raise ctypes.WinError(5)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=replace
        ), patch.object(
            _windows_atomic_replace, "_close_handle", side_effect=close_then_fail
        ):
            with self.assertRaises(BaseException) as raised:
                self.editor.apply(plan)

        self.assertEqual(replace_calls, 1)
        self.assertEqual(target.read_bytes(), b"after")
        self.assertTrue(
            getattr(raised.exception, "publication_committed", False)
        )

    def test_uncertain_1177_preserves_foreign_target_and_recovery_backup(self) -> None:
        target = self.root / "readonly.py"
        target.write_bytes(b"before")
        os.chmod(target, stat.S_IREAD)
        snapshot = WorkspaceSnapshot((SnapshotEntry(target.name, b"after", True),))

        def partial_with_foreign(final: Path, temporary: Path, backup: Path) -> None:
            os.replace(final, backup)
            final.write_bytes(b"foreign")
            raise ctypes.WinError(1177)

        try:
            with patch.object(
                _windows_atomic_replace,
                "_replace_file",
                side_effect=partial_with_foreign,
            ):
                with self.assertRaisesRegex(
                    WorkspaceError, "partially completed"
                ) as raised:
                    self.editor.restore(snapshot)

            self.assertEqual(target.read_bytes(), b"foreign")
            self.assertTrue(target.stat().st_mode & stat.S_IWRITE)
            backups = tuple(self.root.glob(f"{TEMP_PREFIX}*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"before")
            self.assertFalse(backups[0].stat().st_mode & stat.S_IWRITE)
            self.assertIn(str(backups[0]), str(raised.exception))
        finally:
            if target.exists():
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)


if __name__ == "__main__":
    unittest.main()
