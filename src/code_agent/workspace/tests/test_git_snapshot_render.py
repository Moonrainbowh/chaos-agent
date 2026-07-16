from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace._git_diff_snapshot import _render_file  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402


def run_git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    ).stdout


class GitSnapshotRenderTests(unittest.TestCase):
    def test_special_paths_use_git_c_style_quoted_tokens(self) -> None:
        cases = (
            ("tab\tname.txt", '"a/tab\\tname.txt"', '"b/tab\\tname.txt"'),
            ("line\nname.txt", '"a/line\\nname.txt"', '"b/line\\nname.txt"'),
            ('quote"name.txt', '"a/quote\\"name.txt"', '"b/quote\\"name.txt"'),
            ("back\\slash.txt", '"a/back\\\\slash.txt"', '"b/back\\\\slash.txt"'),
            ("café.txt", '"a/caf\\303\\251.txt"', '"b/caf\\303\\251.txt"'),
            ("space name.txt", '"a/space name.txt"', '"b/space name.txt"'),
        )
        for path, old_token, new_token in cases:
            with self.subTest(path=path):
                rendered = _render_file(path, b"content\n")
                self.assertIn(f"diff --git {old_token} {new_token}\n", rendered)
                self.assertIn(f"+++ {new_token}\n", rendered)
                binary = _render_file(path, b"binary\0content")
                self.assertIn(f"diff --git {old_token} {new_token}\n", binary)
                self.assertIn(f"Binary files /dev/null and {new_token} differ", binary)

    @unittest.skipIf(os.name == "nt", "Windows forbids control characters in names")
    def test_control_character_path_patch_passes_git_apply_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source"
            source.mkdir()
            run_git(source, "init", "-q")
            run_git(source, "config", "core.autocrlf", "false")
            name = 'tab\tline\nquote"back\\café.txt'
            (source / name).write_bytes(b"content\n")
            patch_bytes = GitWorkspace(source).diff_snapshot().untracked.encode("utf-8")

            target = base / "target"
            target.mkdir()
            run_git(target, "init", "-q")
            run_git(target, "config", "core.autocrlf", "false")
            checked = _apply(target, patch_bytes, "--check")
            self.assertEqual(
                checked.returncode,
                0,
                checked.stderr.decode("utf-8", errors="replace"),
            )

    def test_empty_untracked_patch_applies_exact_bytes(self) -> None:
        self._assert_untracked_patch_applies("empty.txt", b"")

    def test_no_eol_untracked_patch_applies_exact_bytes(self) -> None:
        self._assert_untracked_patch_applies("no-eol.txt", b"tail-without-eol")

    def _assert_untracked_patch_applies(self, path: str, content: bytes) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source"
            source.mkdir()
            run_git(source, "init", "-q")
            run_git(source, "config", "core.autocrlf", "false")
            (source / path).write_bytes(content)
            snapshot = GitWorkspace(source).diff_snapshot()
            patch_bytes = snapshot.untracked.encode("utf-8")

            target = base / "target"
            target.mkdir()
            run_git(target, "init", "-q")
            run_git(target, "config", "core.autocrlf", "false")
            self.assertEqual(run_git(target, "status", "--porcelain"), b"")
            checked = _apply(target, patch_bytes, "--check")
            self.assertEqual(
                checked.returncode,
                0,
                checked.stderr.decode("utf-8", errors="replace"),
            )
            applied = _apply(target, patch_bytes)
            self.assertEqual(
                applied.returncode,
                0,
                applied.stderr.decode("utf-8", errors="replace"),
            )
            self.assertTrue((target / path).is_file())
            self.assertEqual((target / path).read_bytes(), content)


def _apply(
    root: Path, patch_bytes: bytes, option: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    arguments = ["git", "apply"]
    if option is not None:
        arguments.append(option)
    arguments.append("-")
    return subprocess.run(
        arguments,
        cwd=root,
        input=patch_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


if __name__ == "__main__":
    unittest.main()
