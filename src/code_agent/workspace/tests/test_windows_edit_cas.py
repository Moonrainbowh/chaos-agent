from __future__ import annotations

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

from code_agent.workspace import _secure_replace  # noqa: E402
from code_agent.workspace import _windows_atomic_replace  # noqa: E402
from code_agent.workspace._secure_temp import TEMP_PREFIX  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.errors import EditConflictError, WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows compare-and-swap semantics")
class WindowsEditCompareAndSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_existing_user_write_after_outer_validation_is_not_overwritten(self) -> None:
        self._assert_interleaved_user_content_survives(existed=True)

    def test_concurrent_create_after_outer_validation_is_not_overwritten(self) -> None:
        self._assert_interleaved_user_content_survives(existed=False)

    def test_atomic_save_cannot_replace_guarded_target(self) -> None:
        target = self.root / "module.py"
        foreign = self.root / "foreign.py"
        target.write_bytes(b"before\n")
        foreign.write_bytes(b"user-atomic-save\n")
        plan = self.editor.plan_write(target.name, "agent\n")
        real_replace = _windows_atomic_replace._replace_file
        blocked: list[int] = []

        def try_atomic_save(final: Path, temporary: Path, backup: Path) -> None:
            try:
                os.replace(foreign, final)
            except OSError as error:
                blocked.append(error.winerror)
            else:
                self.fail("atomic save unexpectedly replaced the guarded target")
            real_replace(final, temporary, backup)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=try_atomic_save
        ):
            self.editor.apply(plan)

        self.assertIn(blocked, ([5], [32]))
        self.assertEqual(target.read_bytes(), b"agent\n")
        self.assertEqual(foreign.read_bytes(), b"user-atomic-save\n")
        self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())

    def test_rename_away_and_recreate_is_rolled_back_without_data_loss(self) -> None:
        target = self.root / "module.py"
        away = self.root / "user-renamed.py"
        target.write_bytes(b"before\n")
        plan = self.editor.plan_write(target.name, "agent\n")
        real_replace = _windows_atomic_replace._replace_file

        def rename_and_recreate(final: Path, temporary: Path, backup: Path) -> None:
            os.rename(final, away)
            final.write_bytes(b"user-new\n")
            real_replace(final, temporary, backup)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=rename_and_recreate
        ):
            with self.assertRaises(WorkspaceError):
                self.editor.apply(plan)

        self.assertEqual(target.read_bytes(), b"user-new\n")
        self.assertEqual(away.read_bytes(), b"before\n")
        self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())

    def test_rename_away_restores_readonly_mode_on_original_file(self) -> None:
        target = self.root / "readonly.py"
        away = self.root / "readonly-renamed.py"
        target.write_bytes(b"before\n")
        os.chmod(target, stat.S_IREAD)
        plan = self.editor.plan_write(target.name, "agent\n")
        real_replace = _windows_atomic_replace._replace_file

        def rename_and_recreate(final: Path, temporary: Path, backup: Path) -> None:
            os.rename(final, away)
            final.write_bytes(b"user-new\n")
            real_replace(final, temporary, backup)

        try:
            with patch.object(
                _windows_atomic_replace,
                "_replace_file",
                side_effect=rename_and_recreate,
            ):
                with self.assertRaises(EditConflictError):
                    self.editor.apply(plan)

            self.assertEqual(target.read_bytes(), b"user-new\n")
            self.assertEqual(away.read_bytes(), b"before\n")
            self.assertFalse(away.stat().st_mode & stat.S_IWRITE)
        finally:
            if away.exists():
                os.chmod(away, stat.S_IWRITE | stat.S_IREAD)

    def _assert_interleaved_user_content_survives(self, *, existed: bool) -> None:
        target = self.root / "module.py"
        if existed:
            target.write_bytes(b"before\n")
        plan = self.editor.plan_write(target.name, "agent\n")
        injected = False

        def interleaved_retry(action, **kwargs):
            nonlocal injected
            validate = kwargs["validate"]
            validate()
            target.write_bytes(b"user-concurrent\n")
            injected = True
            return action()

        with patch.object(
            _secure_replace,
            "retry_windows_file_operation",
            interleaved_retry,
        ):
            with self.assertRaises(EditConflictError):
                self.editor.apply(plan)

        self.assertTrue(injected)
        self.assertEqual(target.read_bytes(), b"user-concurrent\n")
        self.assertEqual(tuple(self.root.glob(f"{TEMP_PREFIX}*")), ())


if __name__ == "__main__":
    unittest.main()
