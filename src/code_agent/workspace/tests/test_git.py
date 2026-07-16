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

    def test_diff_snapshot_exposes_all_facets_and_excludes_ignored_files(self) -> None:
        self.initialize_repository()
        unstaged = self.root / "unstaged.txt"
        unstaged.write_text("old\n", encoding="utf-8")
        run_git(self.root, "add", "unstaged.txt")
        run_git(self.root, "commit", "-q", "-m", "add unstaged baseline")
        unstaged.write_text("new\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        run_git(self.root, "add", "staged.txt")
        (self.root / "untracked.txt").write_text("héllo\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (self.root / "ignored.txt").write_text("ignore me\n", encoding="utf-8")

        snapshot = GitWorkspace(self.root).diff_snapshot()

        self.assertIn("staged.txt", snapshot.staged)
        self.assertNotIn("diff --git a/unstaged.txt", snapshot.staged)
        self.assertIn("unstaged.txt", snapshot.unstaged)
        self.assertNotIn("diff --git a/staged.txt", snapshot.unstaged)
        self.assertIn("--- /dev/null", snapshot.untracked)
        self.assertIn("+++ b/untracked.txt", snapshot.untracked)
        self.assertIn("+héllo", snapshot.untracked)
        self.assertEqual(snapshot.untracked_paths, (".gitignore", "untracked.txt"))
        self.assertNotIn("+++ b/ignored.txt", snapshot.untracked)

    def test_diff_snapshot_sorts_untracked_paths_and_marks_binary_without_bytes(self) -> None:
        self.initialize_repository()
        (self.root / "z.txt").write_text("last\n", encoding="utf-8")
        (self.root / "a.bin").write_bytes(b"SECRET_BINARY\x00payload")
        workspace = GitWorkspace(self.root)

        first = workspace.diff_snapshot()
        second = workspace.diff_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(first.untracked_paths, ("a.bin", "z.txt"))
        self.assertIn("Binary files /dev/null and b/a.bin differ", first.untracked)
        self.assertNotIn("SECRET_BINARY", first.untracked)
        self.assertLess(first.untracked.index("b/a.bin"), first.untracked.index("b/z.txt"))

    def test_diff_snapshot_uses_only_fixed_argv(self) -> None:
        self.initialize_repository()
        (self.root / "tracked.txt").write_text("new\n", encoding="utf-8")
        real_popen = subprocess.Popen

        with patch(
            "code_agent.workspace.git.subprocess.Popen", wraps=real_popen
        ) as invoked:
            GitWorkspace(self.root).diff_snapshot(("tracked.txt",))

        commands = [call.args[0] for call in invoked.call_args_list]
        expected = [
            ["diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only", "-z", "--no-renames", "--", "tracked.txt"],
            ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "--no-renames", "--", "tracked.txt"],
            ["diff", "--no-ext-diff", "--no-textconv", "--cached", "--", "tracked.txt"],
            ["diff", "--no-ext-diff", "--no-textconv", "--", "tracked.txt"],
            ["ls-files", "--others", "--exclude-standard", "-z", "--", "tracked.txt"],
        ]
        self.assertEqual([command[4:] for command in commands], expected)
        for call in invoked.call_args_list:
            self.assertEqual(call.args[0][1:4], ["-c", "core.pager=cat", "--literal-pathspecs"])
            self.assertIs(call.kwargs["shell"], False)

    def test_diff_snapshot_path_filters_are_literal(self) -> None:
        self.initialize_repository()
        source = self.root / "a.py"
        source.write_text("old\n", encoding="utf-8")
        run_git(self.root, "add", "a.py")
        run_git(self.root, "commit", "-q", "-m", "add source")
        source.write_text("new\n", encoding="utf-8")
        (self.root / "b.py").write_text("untracked\n", encoding="utf-8")
        workspace = GitWorkspace(self.root)

        self.assertEqual(workspace.diff_snapshot(("*.py",)), GitDiffSnapshot())
        self.assertEqual(workspace.diff_snapshot((":(glob)*.py",)), GitDiffSnapshot())
        self.assertIn("a.py", workspace.diff_snapshot(("a.py",)).unstaged)

    def test_diff_snapshot_rejects_unsafe_filters_before_git(self) -> None:
        self.initialize_repository()
        workspace = GitWorkspace(self.root)
        with patch("code_agent.workspace.git.subprocess.Popen") as invoked:
            with self.assertRaises(SensitivePathError):
                workspace.diff_snapshot((".git/config",))
            with self.assertRaises(PathOutsideWorkspace):
                workspace.diff_snapshot((self.root.parent / "outside.txt",))
        invoked.assert_not_called()

    def test_diff_snapshot_rejects_binary_sensitive_tracked_facets(self) -> None:
        self.initialize_repository()
        secret = self.root / ".env"
        secret.write_bytes(b"old\x00")
        run_git(self.root, "add", ".env")
        run_git(self.root, "commit", "-q", "-m", "add sensitive baseline")
        secret.write_bytes(b"SECRET_UNSTAGED\x00")
        workspace = GitWorkspace(self.root)

        with self.subTest(facet="unstaged"):
            with self.assertRaises(SensitivePathError):
                workspace.diff_snapshot()
        run_git(self.root, "add", ".env")
        with self.subTest(facet="staged"):
            with self.assertRaises(SensitivePathError):
                workspace.diff_snapshot()
        run_git(self.root, "commit", "-q", "-m", "update sensitive baseline")
        run_git(self.root, "mv", ".env", "safe.bin")
        with self.subTest(facet="rename"):
            with self.assertRaises(SensitivePathError):
                workspace.diff_snapshot()

    def test_diff_snapshot_reguards_every_path_reported_by_git(self) -> None:
        self.initialize_repository()
        workspace = GitWorkspace(self.root)
        empty = SimpleNamespace(argv=("git",), returncode=0, stdout=b"", stderr=b"")

        for raw, expected in (
            (b"../outside.txt\x00", PathOutsideWorkspace),
            (b".git/config\x00", SensitivePathError),
        ):
            listed = SimpleNamespace(argv=("git",), returncode=0, stdout=raw, stderr=b"")
            with self.subTest(raw=raw):
                with patch.object(
                    workspace, "_invoke", side_effect=(empty, empty, empty, empty, listed)
                ):
                    with self.assertRaises(expected):
                        workspace.diff_snapshot()


if __name__ == "__main__":
    unittest.main()
