from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.repo_index import RepoIndexService  # noqa: E402
from code_agent.context.repo_scan import (  # noqa: E402
    RepoFileFacts,
    RepoFileScanner,
)
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoIndexServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            guard, IgnoreRules.from_workspace(self.root)
        )
        self.scanner = RepoFileScanner(self.files)
        self.scan_counts: dict[str, int] = {}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def scan(self, path: str) -> RepoFileFacts:
        self.scan_counts[path] = self.scan_counts.get(path, 0) + 1
        return self.scanner.scan(path)

    def index(self) -> RepoIndexService:
        return RepoIndexService(
            self.files,
            max_files=100,
            scan_file=self.scan,
        )

    def test_initializes_once_and_returns_the_same_immutable_snapshot(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("b.py", "def beta():\n    pass\n")
        index = self.index()

        first = index.snapshot_for_turn()
        second = index.snapshot_for_turn()

        self.assertIs(first, second)
        self.assertEqual(first.generation, 1)
        self.assertEqual(
            [entry.path for entry in first.entries],
            ["a.py", "b.py"],
        )
        self.assertEqual(self.scan_counts, {"a.py": 1, "b.py": 1})

    def test_exact_dirty_path_reparses_only_the_changed_file(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("b.py", "def beta():\n    pass\n")
        index = self.index()
        index.snapshot_for_turn()
        self.write(
            "a.py",
            "def alpha_changed_with_longer_name():\n    pass\n",
        )

        index.invalidate(("a.py",))
        updated = index.snapshot_for_turn()

        by_path = {entry.path: entry for entry in updated.entries}
        self.assertEqual(updated.generation, 2)
        self.assertEqual(
            by_path["a.py"].symbols[0].name,
            "alpha_changed_with_longer_name",
        )
        self.assertEqual(self.scan_counts, {"a.py": 2, "b.py": 1})

    def test_add_and_delete_reresolve_dependencies_without_rescanning_importer(self) -> None:
        self.write("consumer.py", "import provider\n")
        index = self.index()
        first = index.snapshot_for_turn()
        self.assertEqual(first.entries[0].dependencies, ())
        self.write("provider.py", "def provide():\n    pass\n")

        index.invalidate(("provider.py",))
        added = index.snapshot_for_turn()

        added_by_path = {entry.path: entry for entry in added.entries}
        self.assertEqual(
            added_by_path["consumer.py"].dependencies,
            ("provider.py",),
        )
        self.assertEqual(
            self.scan_counts,
            {"consumer.py": 1, "provider.py": 1},
        )
        (self.root / "provider.py").unlink()

        index.invalidate(("provider.py",))
        removed = index.snapshot_for_turn()

        self.assertEqual(removed.generation, 3)
        self.assertEqual(
            [entry.path for entry in removed.entries],
            ["consumer.py"],
        )
        self.assertEqual(removed.entries[0].dependencies, ())
        self.assertEqual(
            self.scan_counts,
            {"consumer.py": 1, "provider.py": 1},
        )

    def test_full_reconcile_scans_only_added_and_modified_files(self) -> None:
        self.write("src/a.py", "def alpha():\n    pass\n")
        self.write("src/b.py", "def beta():\n    pass\n")
        self.write("src/c.py", "def gamma():\n    pass\n")
        index = self.index()
        index.snapshot_for_turn()
        self.write(
            "src/a.py",
            "def alpha_changed_with_longer_name():\n    pass\n",
        )
        (self.root / "src" / "b.py").unlink()
        self.write("src/d.py", "def delta():\n    pass\n")

        index.invalidate(())
        reconciled = index.snapshot_for_turn()

        self.assertEqual(reconciled.generation, 2)
        self.assertEqual(
            [entry.path for entry in reconciled.entries],
            ["src/a.py", "src/c.py", "src/d.py"],
        )
        self.assertEqual(
            self.scan_counts,
            {
                "src/a.py": 2,
                "src/b.py": 1,
                "src/c.py": 1,
                "src/d.py": 1,
            },
        )

    def test_exact_add_keeps_the_index_bounded_and_prefers_the_dirty_path(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("z.py", "def zeta():\n    pass\n")
        index = RepoIndexService(
            self.files,
            max_files=2,
            scan_file=self.scan,
        )
        index.snapshot_for_turn()
        self.write("m.py", "def most_recent():\n    pass\n")

        index.invalidate(("m.py",))
        updated = index.snapshot_for_turn()

        self.assertEqual(len(updated.entries), 2)
        self.assertIn("m.py", {entry.path for entry in updated.entries})
        self.assertEqual(self.scan_counts["m.py"], 1)

    def test_reconcile_and_exact_dirty_path_are_coalesced_without_losing_dirty(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        index = RepoIndexService(
            self.files,
            max_files=1,
            scan_file=self.scan,
        )
        index.snapshot_for_turn()
        index.invalidate(())
        self.write("z.py", "def newest():\n    pass\n")
        index.invalidate(("z.py",))

        updated = index.snapshot_for_turn()

        self.assertEqual(
            [entry.path for entry in updated.entries],
            ["z.py"],
        )
        self.assertEqual(updated.entries[0].symbols[0].name, "newest")
        self.assertIs(updated, index.snapshot_for_turn())

    def test_dirty_path_queued_before_initialization_is_not_lost(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        index = RepoIndexService(
            self.files,
            max_files=1,
            scan_file=self.scan,
        )
        self.write("z.py", "def newest():\n    pass\n")
        index.invalidate(("z.py",))

        initialized = index.snapshot_for_turn()

        self.assertEqual(
            [entry.path for entry in initialized.entries],
            ["z.py"],
        )
        self.assertEqual(initialized.entries[0].symbols[0].name, "newest")
        self.assertIs(initialized, index.snapshot_for_turn())

    def test_invalidation_during_refresh_is_nonblocking_and_not_lost(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("b.py", "def beta():\n    pass\n")
        scan_started = threading.Event()
        release_scan = threading.Event()
        block_a = False

        def controlled_scan(path: str) -> RepoFileFacts:
            if block_a and path == "a.py":
                scan_started.set()
                release_scan.wait(2)
            return self.scan(path)

        index = RepoIndexService(
            self.files,
            max_files=100,
            scan_file=controlled_scan,
        )
        index.snapshot_for_turn()
        self.write("a.py", "def alpha_changed():\n    pass\n")
        self.write("b.py", "def beta_changed():\n    pass\n")
        index.invalidate(("a.py",))
        block_a = True

        with ThreadPoolExecutor(max_workers=1) as executor:
            refresh = executor.submit(index.snapshot_for_turn)
            self.assertTrue(scan_started.wait(1))
            invalidated = threading.Event()
            thread = threading.Thread(
                target=lambda: (
                    index.invalidate(("b.py",)),
                    invalidated.set(),
                )
            )
            thread.start()
            try:
                self.assertTrue(invalidated.wait(0.5))
            finally:
                release_scan.set()
            refresh.result(timeout=2)
            thread.join(timeout=1)

        updated = index.snapshot_for_turn()
        by_path = {entry.path: entry for entry in updated.entries}
        self.assertEqual(by_path["a.py"].symbols[0].name, "alpha_changed")
        self.assertEqual(by_path["b.py"].symbols[0].name, "beta_changed")
        self.assertEqual(self.scan_counts, {"a.py": 2, "b.py": 2})

    def test_concurrent_first_access_publishes_one_generation(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("b.py", "def beta():\n    pass\n")
        count_lock = threading.Lock()

        def slow_scan(path: str) -> RepoFileFacts:
            with count_lock:
                self.scan_counts[path] = self.scan_counts.get(path, 0) + 1
            time.sleep(0.02)
            return self.scanner.scan(path)

        index = RepoIndexService(
            self.files,
            max_files=100,
            scan_file=slow_scan,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            snapshots = tuple(
                executor.map(lambda _: index.snapshot_for_turn(), range(4))
            )

        self.assertTrue(all(item is snapshots[0] for item in snapshots))
        self.assertEqual(snapshots[0].generation, 1)
        self.assertEqual(self.scan_counts, {"a.py": 1, "b.py": 1})

if __name__ == "__main__":
    unittest.main()
