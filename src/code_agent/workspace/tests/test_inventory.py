from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import (  # noqa: E402
    FileTooLargeError,
    PathOutsideWorkspace,
    SearchTimeoutError,
    WorkspaceScanLimitError,
)
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace.inventory import (  # noqa: E402
    WorkspaceInventory,
    workspace_fingerprint,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


class FixedPaths:
    def __init__(self, *paths: str) -> None:
        self.paths = paths

    def snapshot_paths(self, *, timeout_s: float | None = None) -> tuple[str, ...]:
        del timeout_s
        return self.paths


class WorkspaceInventoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize_repository(self) -> None:
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "user.name", "Inventory Tests")
        run_git(self.root, "config", "user.email", "inventory@example.invalid")

    def write(self, relative_path: str, content: bytes) -> None:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


class InventoryCaptureTests(WorkspaceInventoryTestCase):
    def test_includes_tracked_and_nonignored_untracked_only(self) -> None:
        self.initialize_repository()
        self.write("tracked.py", b"old")
        run_git(self.root, "add", "tracked.py")
        run_git(self.root, "commit", "-q", "-m", "base")
        self.write("visible.py", b"new")
        self.write("ignored.tmp", b"cache")
        self.write(".gitignore", b"*.tmp\nignored-dir/\n")
        self.write("ignored-dir/nested.py", b"cache")

        inventory = WorkspaceInventory.capture(
            self.root, self.guard, GitWorkspace(self.root)
        )

        self.assertEqual(inventory.paths, (".gitignore", "tracked.py", "visible.py"))

    def test_deleted_tracked_file_is_represented_by_absence(self) -> None:
        self.initialize_repository()
        self.write("tracked.py", b"old")
        run_git(self.root, "add", "tracked.py")
        run_git(self.root, "commit", "-q", "-m", "base")
        before = WorkspaceInventory.capture(
            self.root, self.guard, GitWorkspace(self.root)
        )

        (self.root / "tracked.py").unlink()
        deleted = WorkspaceInventory.capture(
            self.root, self.guard, GitWorkspace(self.root)
        )

        self.assertEqual(deleted.paths, ())
        self.assertNotEqual(deleted.digest, before.digest)

    def test_sensitive_and_git_metadata_paths_are_excluded(self) -> None:
        for name in ("safe.py", ".env", "id_ed25519", "server.pem"):
            self.write(name, name.encode("utf-8"))

        inventory = WorkspaceInventory.capture(
            self.root,
            self.guard,
            FixedPaths("server.pem", ".git/config", "safe.py", ".env", "id_ed25519"),
        )

        self.assertEqual(inventory.paths, ("safe.py",))

    def test_symlink_path_is_rejected(self) -> None:
        self.write("linked.py", b"content")

        with patch.object(
            Path, "is_symlink", new=lambda path: path.name == "linked.py"
        ):
            with self.assertRaises(PathOutsideWorkspace):
                WorkspaceInventory.capture(
                    self.root, self.guard, FixedPaths("linked.py")
                )

    def test_reparse_path_is_rejected(self) -> None:
        self.write("linked.py", b"content")
        reparse = SimpleNamespace(st_file_attributes=0x400)

        with patch.object(Path, "is_symlink", new=lambda path: False):
            with patch.object(Path, "lstat", return_value=reparse):
                with self.assertRaises(PathOutsideWorkspace):
                    WorkspaceInventory.capture(
                        self.root, self.guard, FixedPaths("linked.py")
                    )

    def test_duplicate_paths_are_rejected(self) -> None:
        self.write("same.py", b"content")

        with self.assertRaisesRegex(ValueError, "duplicate inventory path"):
            WorkspaceInventory.capture(
                self.root, self.guard, FixedPaths("same.py", "same.py")
            )

    def test_file_count_limit_is_enforced(self) -> None:
        self.write("one.py", b"1")
        self.write("two.py", b"2")

        with self.assertRaises(WorkspaceScanLimitError):
            WorkspaceInventory.capture(
                self.root,
                self.guard,
                FixedPaths("one.py", "two.py"),
                max_files=1,
            )

    def test_total_byte_limit_is_enforced(self) -> None:
        self.write("one.py", b"123")
        self.write("two.py", b"456")

        with self.assertRaises(FileTooLargeError):
            WorkspaceInventory.capture(
                self.root,
                self.guard,
                FixedPaths("one.py", "two.py"),
                max_total_bytes=5,
            )

    def test_global_deadline_is_enforced(self) -> None:
        self.write("slow.py", b"content")

        with patch(
            "code_agent.workspace.inventory.time.monotonic", side_effect=(0.0, 2.0)
        ):
            with self.assertRaises(SearchTimeoutError):
                WorkspaceInventory.capture(
                    self.root,
                    self.guard,
                    FixedPaths("slow.py"),
                    deadline_s=1.0,
                )

    def test_entries_and_digest_are_deterministic(self) -> None:
        self.write("a.py", b"a")
        self.write("b.py", b"bb")

        first = WorkspaceInventory.capture(
            self.root, self.guard, FixedPaths("b.py", "a.py")
        )
        second = WorkspaceInventory.capture(
            self.root, self.guard, FixedPaths("a.py", "b.py")
        )

        self.assertEqual(first.entries, second.entries)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(workspace_fingerprint(first), first.digest)
        self.assertEqual(first.paths, ("a.py", "b.py"))
        self.assertEqual(first.entries[0].size, 1)
        self.assertEqual(first.entries[0].mode, os.stat(self.root / "a.py").st_mode & 0o7777)


if __name__ == "__main__":
    unittest.main()
