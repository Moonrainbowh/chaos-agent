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

from code_agent.workspace.errors import PathOutsideWorkspace, WorkspaceError  # noqa: E402
from code_agent.workspace.git import (  # noqa: E402
    GitCommandError,
    GitWorkspace,
    _decode_path_list,
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

    def test_snapshot_path_decoder_rejects_non_utf8_git_output(self) -> None:
        with self.assertRaisesRegex(GitCommandError, "undecodable path") as raised:
            _decode_path_list(b"valid.py\0\xff.py\0")

        self.assertEqual(raised.exception.operation, "snapshot_paths")
        self.assertEqual(raised.exception.stderr, "invalid UTF-8 path")


if __name__ == "__main__":
    unittest.main()
