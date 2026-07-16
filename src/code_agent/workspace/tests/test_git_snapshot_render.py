from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

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
