from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import (  # noqa: E402
    PathOutsideWorkspace,
    SensitivePathError,
    WorkspaceError,
)
from code_agent.workspace.git import (  # noqa: E402
    GitCommandError,
    GitDiffSnapshot,
    GitWorkspace,
    _decode_path_list,
)
from code_agent.workspace._git_errors import (  # noqa: E402
    decode_git_output,
    decode_git_text,
)


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


class GitWorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize_repository(self) -> None:
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "user.name", "Workspace Tests")
        run_git(self.root, "config", "user.email", "workspace@example.invalid")
        (self.root / "tracked.txt").write_bytes(b"old\n")
        run_git(self.root, "add", "tracked.txt")
        run_git(self.root, "commit", "-q", "-m", "initial")


class GitRepositoryTests(GitWorkspaceTestCase):
    def test_diff_snapshot_defaults_are_frozen_and_independent(self) -> None:
        first = GitDiffSnapshot()
        second = GitDiffSnapshot()

        self.assertEqual(first.staged, "")
        self.assertEqual(first.unstaged, "")
        self.assertEqual(first.untracked, "")
        self.assertEqual(first.untracked_paths, ())
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.staged = "changed"  # type: ignore[misc]

    def test_constructor_does_not_accept_a_git_executable_override(self) -> None:
        with self.assertRaises(TypeError):
            GitWorkspace(self.root, git_executable="untrusted-program")

    def test_constructor_resolves_only_the_fixed_git_name(self) -> None:
        resolved_git = str(self.root / "trusted-git.exe")

        with patch("shutil.which", return_value=resolved_git) as finder:
            workspace = GitWorkspace(self.root)

        finder.assert_called_once_with("git")
        self.assertEqual(workspace._git_executable, resolved_git)

    def test_constructor_validates_timeout(self) -> None:
        self.assertEqual(GitWorkspace(self.root).timeout_s, 30.0)

        for invalid in (0, -1, float("inf"), float("nan"), True, "1"):
            with self.subTest(timeout_s=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    GitWorkspace(self.root, timeout_s=invalid)  # type: ignore[arg-type]

    def test_non_repository_is_false_and_commands_raise_structured_error(self) -> None:
        workspace = GitWorkspace(self.root)

        self.assertFalse(workspace.is_repository())
        with self.assertRaises(GitCommandError) as raised:
            workspace.status_porcelain()

        error = raised.exception
        self.assertIsInstance(error, WorkspaceError)
        self.assertEqual(error.operation, "status")
        self.assertIsInstance(error.argv, tuple)
        self.assertIsNotNone(error.returncode)
        self.assertTrue(error.stderr.strip())

    def test_status_and_diff_use_real_temporary_repository(self) -> None:
        self.initialize_repository()
        (self.root / "tracked.txt").write_bytes(b"new\n")
        (self.root / "untracked.txt").write_text("new", encoding="utf-8")
        workspace = GitWorkspace(self.root)

        self.assertTrue(workspace.is_repository())
        status = workspace.status_porcelain()
        diff = workspace.diff(("tracked.txt",))

        self.assertIn(" M tracked.txt", status)
        self.assertIn("?? untracked.txt", status)
        self.assertIn("-old", diff)
        self.assertIn("+new", diff)
        self.assertNotIn("untracked.txt", diff)

    def test_diff_uses_safe_argv_and_does_not_execute_external_diff(self) -> None:
        self.initialize_repository()
        (self.root / "tracked.txt").write_bytes(b"new\n")
        marker = self.root / "external-ran.txt"
        external = self.root / "external.cmd"
        external.write_text(f"@echo ran>{marker}\n", encoding="utf-8")
        real_popen = subprocess.Popen

        with patch.dict(os.environ, {"GIT_EXTERNAL_DIFF": str(external)}):
            with patch(
                "code_agent.workspace.git.subprocess.Popen", wraps=real_popen
            ) as invoked:
                GitWorkspace(self.root).diff(("tracked.txt",))

        argv = invoked.call_args.args[0]
        self.assertTrue(Path(argv[0]).name.casefold().startswith("git"))
        self.assertEqual(argv[1:3], ["-c", "core.pager=cat"])
        if os.name == "nt":
            self.assertIn("core.longPaths=true", argv)
        self.assertLess(argv.index("--literal-pathspecs"), argv.index("diff"))
        self.assertIn("--no-ext-diff", argv)
        self.assertIn("--no-textconv", argv)
        separator = argv.index("--")
        self.assertEqual(argv[separator + 1 :], ["tracked.txt"])
        self.assertIs(invoked.call_args.kwargs["shell"], False)
        self.assertFalse(marker.exists())

    def test_diff_treats_wildcard_and_magic_pathspecs_as_literal(self) -> None:
        self.initialize_repository()
        source = self.root / "a.py"
        source.write_bytes(b"old\n")
        run_git(self.root, "add", "a.py")
        run_git(self.root, "commit", "-q", "-m", "add source")
        source.write_bytes(b"new\n")

        workspace = GitWorkspace(self.root)

        self.assertEqual(workspace.diff(("*.py",)), "")
        self.assertEqual(workspace.diff((":(glob)*.py",)), "")
        self.assertIn("a.py", workspace.diff(("a.py",)))

    def test_diff_rejects_outside_paths_before_invoking_git(self) -> None:
        self.initialize_repository()
        outside = self.root.parent / "outside.txt"

        with self.assertRaises(PathOutsideWorkspace):
            GitWorkspace(self.root).diff((outside,))

    def test_snapshot_paths_include_deleted_tracked_and_nonignored_untracked(self) -> None:
        self.initialize_repository()
        (self.root / "tracked.txt").unlink()
        (self.root / "visible.py").write_bytes(b"new")
        (self.root / "ignored.tmp").write_bytes(b"cache")
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")

        paths = GitWorkspace(self.root).snapshot_paths()

        self.assertEqual(paths, (".gitignore", "tracked.txt", "visible.py"))

    def test_changed_snapshot_paths_exclude_clean_tracked_files(self) -> None:
        self.initialize_repository()
        (self.root / "clean.txt").write_bytes(b"clean\n")
        (self.root / "deleted.txt").write_bytes(b"delete me\n")
        (self.root / ".gitignore").write_bytes(b"*.tmp\n")
        run_git(self.root, "add", "clean.txt", "deleted.txt", ".gitignore")
        run_git(self.root, "commit", "-q", "-m", "add fixtures")
        (self.root / "tracked.txt").write_bytes(b"changed\n")
        (self.root / "deleted.txt").unlink()
        (self.root / "staged.txt").write_bytes(b"staged\n")
        run_git(self.root, "add", "staged.txt")
        (self.root / "visible.py").write_bytes(b"new")
        (self.root / "ignored.tmp").write_bytes(b"cache")

        paths = GitWorkspace(self.root).changed_snapshot_paths()

        self.assertEqual(
            paths,
            ("deleted.txt", "staged.txt", "tracked.txt", "visible.py"),
        )

    def test_changed_snapshot_paths_support_unborn_head(self) -> None:
        run_git(self.root, "init", "-q")
        (self.root / "staged.txt").write_bytes(b"staged\n")
        (self.root / "visible.txt").write_bytes(b"visible\n")
        run_git(self.root, "add", "staged.txt")

        paths = GitWorkspace(self.root).changed_snapshot_paths()

        self.assertEqual(paths, ("staged.txt", "visible.txt"))

    def test_tracked_paths_excludes_ignored_and_untracked_existing_files(self) -> None:
        self.initialize_repository()
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        (self.root / "ignored.tmp").write_bytes(b"ignored")
        (self.root / "visible.txt").write_bytes(b"visible")

        paths = GitWorkspace(self.root).tracked_paths(
            ("tracked.txt", "ignored.tmp", "visible.txt")
        )

        self.assertEqual(paths, ("tracked.txt",))

    def test_snapshot_path_decoder_rejects_non_utf8_git_output(self) -> None:
        with self.assertRaisesRegex(GitCommandError, "undecodable path") as raised:
            _decode_path_list(b"valid.py\0\xff.py\0")

        self.assertEqual(raised.exception.operation, "snapshot_paths")
        self.assertEqual(raised.exception.stderr, "invalid UTF-8 path")

    def test_non_utf8_git_diagnostic_is_lossless_and_explicit(self) -> None:
        decoded = decode_git_output(b"failure \xff")

        self.assertNotIn("\ufffd", decoded)
        self.assertIn("base64=ZmFpbHVyZSD/", decoded)

    def test_non_utf8_git_control_output_preserves_source_bytes(self) -> None:
        raw = b"control \xff"

        with self.assertRaisesRegex(GitCommandError, "non-UTF-8") as raised:
            decode_git_text(raw, "status", ("git", "status"))

        self.assertEqual(raised.exception.stdout_bytes, raw)
        self.assertNotIn("\ufffd", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
