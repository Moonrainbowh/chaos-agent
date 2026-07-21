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

from code_agent.workspace import _secure_replace as replacement  # noqa: E402
from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


def cleanup_function_name() -> str:
    return "_remove_posix_temp" if os.name == "posix" else "_remove_path_temp"


class TempCleanupProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        for path in self.root.glob(".code-agent-edit-*"):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        self.temporary.cleanup()

    def snapshot(self, name: str = "module.py") -> WorkspaceSnapshot:
        (self.root / name).write_bytes(b"before")
        return WorkspaceSnapshot((SnapshotEntry(name, b"after", True),))

    def temp_paths(self) -> list[Path]:
        return list(self.root.glob(".code-agent-edit-*"))

    def test_raw_fstat_proves_ownership_after_identity_helper_failure(self) -> None:
        snapshot = self.snapshot()
        primary = WorkspaceError("handle identity failed")
        real_fstat = os.fstat
        regular_fstats = 0

        def record_fstat(fd: int):
            nonlocal regular_fstats
            metadata = real_fstat(fd)
            if stat.S_ISREG(metadata.st_mode):
                regular_fstats += 1
            return metadata

        with patch.object(replacement.os, "fstat", side_effect=record_fstat):
            with patch.object(replacement, "identity_from_fd", side_effect=primary):
                with self.assertRaises(WorkspaceError) as raised:
                    self.editor.restore(snapshot)

        self.assertIs(raised.exception, primary)
        self.assertGreaterEqual(regular_fstats, 1)
        self.assertEqual(self.temp_paths(), [])

    def test_unproven_ownership_keeps_temp_and_reports_cleanup_failure(self) -> None:
        snapshot = self.snapshot()
        primary = WorkspaceError("handle identity failed")

        with patch.object(
            replacement, "_raw_identity_from_fd", return_value=None, create=True
        ):
            with patch.object(replacement, "identity_from_fd", side_effect=primary):
                with self.assertRaises(WorkspaceError) as raised:
                    self.editor.restore(snapshot)

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(primary), "handle identity failed")
        cleanup_error = getattr(primary, "cleanup_error", None)
        self.assertIsNotNone(cleanup_error, "cleanup failure must be attached")
        self.assertRegex(str(cleanup_error), "cleanup ownership failure")
        self.assertEqual(len(self.temp_paths()), 1)

    def test_replaced_temp_is_not_deleted_during_cleanup(self) -> None:
        snapshot = self.snapshot()
        primary = WorkspaceError("handle identity failed")
        cleanup_name = cleanup_function_name()
        real_cleanup = getattr(replacement, cleanup_name)
        real_replace = os.replace

        def replace_before_cleanup(*args: object, **kwargs: object) -> None:
            temp_path = (
                self.root / str(args[1])
                if os.name == "posix"
                else Path(args[0])
            )
            attacker = self.root / "attacker-source"
            attacker.write_bytes(b"attacker")
            real_replace(attacker, temp_path)
            real_cleanup(*args, **kwargs)

        with patch.object(replacement, "identity_from_fd", side_effect=primary):
            with patch.object(
                replacement, cleanup_name, side_effect=replace_before_cleanup
            ):
                with self.assertRaises(WorkspaceError) as raised:
                    self.editor.restore(snapshot)

        self.assertIs(raised.exception, primary)
        cleanup_error = getattr(primary, "cleanup_error", None)
        self.assertIsNotNone(cleanup_error, "cleanup failure must be attached")
        self.assertRegex(str(cleanup_error), "cleanup ownership failure")
        self.assertEqual(self.temp_paths()[0].read_bytes(), b"attacker")

    def test_cleanup_failure_does_not_mask_primary_exception(self) -> None:
        snapshot = self.snapshot()
        primary = WorkspaceError("primary restore failure")
        cleanup = WorkspaceError("cleanup ownership failure")

        with patch.object(replacement, "identity_from_fd", side_effect=primary):
            with patch.object(
                replacement, cleanup_function_name(), side_effect=cleanup
            ):
                with self.assertRaises(WorkspaceError) as raised:
                    self.editor.restore(snapshot)

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(raised.exception), "primary restore failure")
        self.assertIs(primary.cleanup_error, cleanup)

    def test_native_primary_type_survives_a_cleanup_failure(self) -> None:
        from code_agent.workspace import _posix_io

        snapshot = self.snapshot()
        primary = OSError("primary os failure")
        cleanup = WorkspaceError("cleanup ownership failure")
        replace_target = _posix_io if os.name == "posix" else replacement.os

        with patch.object(replace_target, "replace", side_effect=primary):
            with patch.object(
                replacement, cleanup_function_name(), side_effect=cleanup
            ):
                try:
                    with self.assertRaises(OSError) as raised:
                        self.editor.restore(snapshot)
                except WorkspaceError as error:
                    self.fail(f"native primary was replaced: {error}")

        self.assertIs(raised.exception, primary)
        self.assertEqual(str(raised.exception), "primary os failure")
        self.assertIs(primary.cleanup_error, cleanup)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    def test_posix_cleanup_needs_no_temp_read_permission(self) -> None:
        from code_agent.workspace import _posix_io

        for mode in (0o000, 0o200):
            with self.subTest(mode=oct(mode)):
                name = f"mode-{mode}.py"
                snapshot = self.snapshot(name)
                os.chmod(self.root / name, mode)
                primary = WorkspaceError("replace locked")
                with patch.object(_posix_io, "replace", side_effect=primary):
                    with self.assertRaises(WorkspaceError) as raised:
                        self.editor.restore(snapshot)
                self.assertIs(raised.exception, primary)
                self.assertEqual(self.temp_paths(), [])


if __name__ == "__main__":
    unittest.main()
