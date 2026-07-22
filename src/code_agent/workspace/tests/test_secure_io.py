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

from code_agent.workspace._secure_io import (  # noqa: E402
    PathIdentity,
    canonical_path_key,
)
from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
    build_restore_snapshot,
)
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class StableIdentityTests(unittest.TestCase):
    def test_mode_size_and_mtime_are_not_part_of_object_identity(self) -> None:
        before = PathIdentity(1, 2, stat.S_IFREG | 0o600, 0, 0, 10)
        after = PathIdentity(1, 2, stat.S_IFREG | 0o644, 0, 99, 20)

        self.assertEqual(before, after)

    def test_canonical_key_preserves_case_on_posix(self) -> None:
        with patch("code_agent.workspace._secure_io.os.name", "posix"):
            self.assertNotEqual(canonical_path_key("Foo.py"), canonical_path_key("foo.py"))

    def test_canonical_key_folds_case_on_windows(self) -> None:
        with patch("code_agent.workspace._secure_io.os.name", "nt"):
            self.assertEqual(canonical_path_key("Foo.py"), canonical_path_key("foo.py"))

    def test_restore_plan_allows_case_distinct_posix_paths(self) -> None:
        target = WorkspaceSnapshot(
            (
                SnapshotEntry("Foo.py", b"one", True),
                SnapshotEntry("foo.py", b"two", True),
            )
        )

        with patch("code_agent.workspace._secure_io.os.name", "posix"):
            try:
                restore = build_restore_snapshot((), target)
            except ValueError as error:
                self.fail(f"POSIX case-distinct paths must be accepted: {error}")

        self.assertEqual(len(restore.entries), 2)

    def test_restore_plan_rejects_case_aliases_on_windows(self) -> None:
        target = WorkspaceSnapshot(
            (
                SnapshotEntry("Foo.py", b"one", True),
                SnapshotEntry("foo.py", b"two", True),
            )
        )

        with patch("code_agent.workspace._secure_io.os.name", "nt"):
            with self.assertRaisesRegex(ValueError, "duplicate target path"):
                build_restore_snapshot((), target)


class SecureRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        target = self.root / "readonly.py"
        if target.exists():
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        self.temporary.cleanup()

    def test_replace_result_must_match_the_moved_temp_identity_and_hash(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry("module.py", b"after", True),))
        real_replace = os.replace

        def replace_then_attack(
            source: str | os.PathLike[str],
            destination: str | os.PathLike[str],
            **kwargs: int,
        ) -> None:
            real_replace(source, destination, **kwargs)
            target.write_bytes(b"attacker")

        with patch("os.replace", side_effect=replace_then_attack):
            with self.assertRaisesRegex(WorkspaceError, "changed after replace"):
                self.editor.restore(snapshot)

    def test_temp_created_before_identity_failure_is_cleaned(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry("module.py", b"after", True),))
        before_names = {path.name for path in self.root.iterdir()}
        from code_agent.workspace import _secure_io

        real_inspect = _secure_io._inspect_path

        def fail_temp_identity(path: Path, *, missing_ok: bool, context: str):
            if path.name.startswith(".code-agent-edit-"):
                raise WorkspaceError("temp identity failed")
            return real_inspect(path, missing_ok=missing_ok, context=context)

        with patch.object(_secure_io, "_inspect_path", side_effect=fail_temp_identity):
            with self.assertRaisesRegex(WorkspaceError, "temp identity failed"):
                self.editor.restore(snapshot)

        self.assertEqual({path.name for path in self.root.iterdir()}, before_names)

    def test_temp_is_cleaned_when_handle_identity_capture_fails(self) -> None:
        target = self.root / "module.py"
        target.write_bytes(b"before")
        snapshot = WorkspaceSnapshot((SnapshotEntry("module.py", b"after", True),))
        before_names = {path.name for path in self.root.iterdir()}

        with patch(
            "code_agent.workspace._secure_replace.identity_from_fd",
            side_effect=WorkspaceError("handle identity failed"),
        ):
            with self.assertRaisesRegex(WorkspaceError, "handle identity failed"):
                self.editor.restore(snapshot)

        self.assertEqual({path.name for path in self.root.iterdir()}, before_names)

    def test_readonly_temp_is_cleaned_when_replace_is_locked(self) -> None:
        target = self.root / "readonly.py"
        target.write_bytes(b"before")
        os.chmod(target, stat.S_IREAD)
        snapshot = WorkspaceSnapshot((SnapshotEntry("readonly.py", b"after", True),))
        before_names = {path.name for path in self.root.iterdir()}

        with patch("os.replace", side_effect=OSError("locked")):
            with self.assertRaisesRegex(WorkspaceError, "atomically restore"):
                self.editor.restore(snapshot)

        self.assertEqual(target.read_bytes(), b"before")
        self.assertEqual({path.name for path in self.root.iterdir()}, before_names)

    @unittest.skipUnless(os.name == "nt", "Windows readonly replacement semantics")
    def test_windows_readonly_target_restores_successfully(self) -> None:
        target = self.root / "readonly.py"
        target.write_bytes(b"before")
        os.chmod(target, stat.S_IREAD)
        snapshot = WorkspaceSnapshot((SnapshotEntry("readonly.py", b"after", True),))

        try:
            self.editor.restore(snapshot)
        except WorkspaceError as error:
            self.fail(f"readonly target should restore safely: {error}")

        self.assertEqual(target.read_bytes(), b"after")

    @unittest.skipUnless(os.name == "posix", "POSIX mode semantics")
    def test_posix_0600_temp_restores_0644_target_mode(self) -> None:
        target = self.root / "mode.py"
        target.write_bytes(b"before")
        os.chmod(target, 0o644)
        snapshot = WorkspaceSnapshot((SnapshotEntry("mode.py", b"after", True),))

        self.editor.restore(snapshot)

        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
