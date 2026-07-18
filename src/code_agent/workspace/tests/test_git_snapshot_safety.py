from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import SensitivePathError, WorkspaceError  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402


def run_git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    ).stdout


class GitSnapshotSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "core.autocrlf", "false")
        run_git(self.root, "config", "user.name", "Snapshot Safety")
        run_git(self.root, "config", "user.email", "safety@example.invalid")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit_file(self, path: str, content: bytes) -> Path:
        absolute = self.root / path
        absolute.write_bytes(content)
        run_git(self.root, "add", path)
        run_git(self.root, "commit", "-q", "-m", f"add {path}")
        return absolute

    def delete_mixed_case_file_and_directory(self) -> str:
        relative = "MiXeDDir/MiXeD.txt"
        directory = self.root / "MiXeDDir"
        directory.mkdir()
        tracked = self.commit_file(relative, b"old\n")
        tracked.unlink()
        directory.rmdir()
        return relative

    @unittest.skipUnless(os.name == "nt", "Windows path semantics only")
    def test_windows_directory_filter_matches_deleted_path_case_insensitively(self) -> None:
        relative = self.delete_mixed_case_file_and_directory()

        snapshot = GitWorkspace(self.root).diff_snapshot(("mixeddir",))

        self.assertIn(f"diff --git a/{relative} b/{relative}", snapshot.unstaged)

    @unittest.skipIf(os.name == "nt", "POSIX path semantics only")
    def test_posix_directory_filter_remains_case_sensitive(self) -> None:
        self.delete_mixed_case_file_and_directory()

        snapshot = GitWorkspace(self.root).diff_snapshot(("mixeddir",))

        self.assertEqual(snapshot.unstaged, "")

    def test_tracked_sensitive_change_after_preflights_is_rejected(self) -> None:
        secret = self.commit_file(".env", b"old\n")
        workspace = GitWorkspace(self.root)
        invoke = workspace._invoke

        def mutate_after_preflight(operation, arguments, max_output_bytes=None):
            result = invoke(operation, arguments, max_output_bytes)
            if operation == "diff_unstaged_paths":
                secret.write_bytes(b"SECRET_AFTER_PREFLIGHT\n")
            return result

        with patch.object(workspace, "_invoke", side_effect=mutate_after_preflight):
            try:
                snapshot = workspace.diff_snapshot()
            except WorkspaceError:
                return

        facets = snapshot.staged + snapshot.unstaged
        self.assertNotIn("SECRET_AFTER_PREFLIGHT", facets)
        self.assertNotIn(".env", facets)

    def test_safe_filter_cannot_hide_sensitive_rename_source(self) -> None:
        self.commit_file(".env", b"secret baseline\n")
        run_git(self.root, "mv", ".env", "safe.txt")

        with self.assertRaises(SensitivePathError):
            GitWorkspace(self.root).diff_snapshot(("safe.txt",))

    def test_invalid_utf8_tracked_patch_is_never_replacement_decoded(self) -> None:
        tracked = self.commit_file("invalid.txt", b"old\n")
        tracked.write_bytes(b"SECRET_PREFIX\xfftail\n")

        try:
            snapshot = GitWorkspace(self.root).diff_snapshot()
        except WorkspaceError:
            return

        facets = snapshot.staged + snapshot.unstaged
        self.assertNotIn("SECRET_PREFIX", facets)
        self.assertNotIn("\ufffd", facets)

    def test_untracked_open_handle_must_match_guarded_file_identity(self) -> None:
        safe = self.root / "note.txt"
        safe.write_bytes(b"safe\n")
        outside = self.base / "outside-secret.txt"
        outside.write_bytes(b"OUTSIDE_SECRET\n")
        outside_identity = _identity(outside.stat())
        opened_identities: list[tuple[int, int, int]] = []

        caught: WorkspaceError | None = None
        patcher = _swapped_open_patch(
            safe, outside, outside_identity, opened_identities
        )
        with patcher:
            try:
                snapshot = GitWorkspace(self.root).diff_snapshot()
            except WorkspaceError as error:
                caught = error

        self.assertIn(outside_identity, opened_identities)
        leaked = caught is None and "OUTSIDE_SECRET" in snapshot.untracked
        self.assertIsInstance(caught, WorkspaceError, f"outside handle accepted; leaked={leaked}")


def _swapped_open_patch(
    safe: Path,
    outside: Path,
    outside_identity: tuple[int, int, int],
    opened_identities: list[tuple[int, int, int]],
):
    if os.name == "nt":
        real_create = windows_open._create_handle

        def swapped_create(file: Path, **options: int) -> int:
            chosen = outside if _same_path(file, safe) else file
            handle = real_create(chosen, **options)
            if chosen == outside:
                opened_identities.append(outside_identity)
            return handle

        return patch.object(
            windows_open, "_create_handle", side_effect=swapped_create
        )
    real_os_open = os.open

    def swapped_os_open(file, flags, mode=0o777, *, dir_fd=None):
        chosen = outside if _same_path(file, safe) else file
        options = {} if dir_fd is None else {"dir_fd": dir_fd}
        descriptor = real_os_open(chosen, flags, mode, **options)
        if chosen == outside:
            opened_identities.append(_identity(os.fstat(descriptor)))
        return descriptor

    return patch("os.open", side_effect=swapped_os_open)


def _same_path(value: object, expected: Path) -> bool:
    try:
        return Path(os.fspath(value)).resolve(strict=False) == expected
    except TypeError:
        return False


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size


if __name__ == "__main__":
    unittest.main()
