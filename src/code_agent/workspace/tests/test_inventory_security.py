from __future__ import annotations

import builtins
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import (  # noqa: E402
    SearchTimeoutError,
    WorkspaceError,
)
from code_agent.workspace.inventory import WorkspaceInventory  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RecordingGit:
    def __init__(self, *paths: str) -> None:
        self.paths = paths
        self.timeout_s: float | None = None

    def snapshot_paths(self, *, timeout_s: float | None = None) -> tuple[str, ...]:
        self.timeout_s = timeout_s
        return self.paths


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowStream:
    def __init__(self, stream: object, clock: MutableClock) -> None:
        self.stream = stream
        self.clock = clock
        self.read_sizes: list[int] = []

    def __enter__(self) -> SlowStream:
        return self

    def __exit__(self, *args: object) -> None:
        self.stream.close()  # type: ignore[attr-defined]

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        value = self.stream.read(size)  # type: ignore[attr-defined]
        self.clock.now = 2.0
        return value

    def fileno(self) -> int:
        return self.stream.fileno()  # type: ignore[attr-defined,no-any-return]


class InventorySecurityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: bytes) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target


class InventoryPolicyTests(InventorySecurityTestCase):
    def test_builtin_cache_directory_is_excluded_without_gitignore(self) -> None:
        self.write("safe.py", b"safe")
        self.write("pkg/__pycache__/module.pyc", b"cache")

        inventory = WorkspaceInventory.capture(
            self.root,
            self.guard,
            RecordingGit("pkg/__pycache__/module.pyc", "safe.py"),
        )

        self.assertEqual(inventory.paths, ("safe.py",))

    def test_casefold_duplicate_git_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate inventory path"):
            WorkspaceInventory.capture(
                self.root, self.guard, RecordingGit("Foo.py", "foo.py")
            )


class InventoryDeadlineTests(InventorySecurityTestCase):
    def test_remaining_deadline_is_passed_to_git(self) -> None:
        git = RecordingGit()
        clock = MutableClock()

        with patch("code_agent.workspace.inventory.time.monotonic", clock):
            inventory = WorkspaceInventory.capture(
                self.root, self.guard, git, deadline_s=0.75
            )

        self.assertEqual(inventory.paths, ())
        self.assertIsNotNone(git.timeout_s)
        self.assertGreater(git.timeout_s or 0.0, 0.0)
        self.assertLessEqual(git.timeout_s or 1.0, 0.75)

    def test_slow_read_uses_chunks_and_stops_at_deadline(self) -> None:
        target = self.write("large.py", b"x" * 100_000)
        real_stream = builtins.open(target, "rb")
        clock = MutableClock()
        slow = SlowStream(real_stream, clock)

        with patch("code_agent.workspace.inventory.time.monotonic", clock):
            with patch.object(Path, "open", return_value=slow):
                with self.assertRaises(SearchTimeoutError):
                    WorkspaceInventory.capture(
                        self.root,
                        self.guard,
                        RecordingGit("large.py"),
                        deadline_s=1.0,
                    )

        self.assertTrue(slow.read_sizes)
        self.assertLessEqual(max(slow.read_sizes), 65_536)


class InventoryRaceTests(InventorySecurityTestCase):
    def test_parent_replacement_during_open_fails_closed(self) -> None:
        parent = self.root / "package"
        displaced = self.root / "original-package"
        target = self.write("package/safe.py", b"safe")
        attacked = False

        def replace_parent(path: Path, mode: str = "r", *args: object, **kwargs: object):
            nonlocal attacked
            if path == target and not attacked:
                attacked = True
                parent.rename(displaced)
                parent.mkdir()
                with builtins.open(parent / "safe.py", "wb") as stream:
                    stream.write(b"attacker")
            return builtins.open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", new=replace_parent):
            with self.assertRaisesRegex(WorkspaceError, "changed during inventory"):
                WorkspaceInventory.capture(
                    self.root, self.guard, RecordingGit("package/safe.py")
                )

        self.assertEqual((displaced / "safe.py").read_bytes(), b"safe")
        self.assertEqual(
            hashlib.sha256((parent / "safe.py").read_bytes()).hexdigest(),
            hashlib.sha256(b"attacker").hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
