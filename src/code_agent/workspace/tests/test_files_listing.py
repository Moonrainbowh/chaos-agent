from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import MAX_INVENTORY_CACHE_ENTRIES

try:
    from ._files_test_support import CountingScandir, WorkspaceFilesTestCase
except ImportError:
    from _files_test_support import CountingScandir, WorkspaceFilesTestCase


class ListFilesTests(WorkspaceFilesTestCase):
    def test_equal_root_listings_reuse_one_bounded_inventory_scan(self) -> None:
        (self.root / "a.py").write_text("a", encoding="utf-8")
        files = self.files()

        with patch.object(
            files, "_iter_files", wraps=files._iter_files
        ) as iter_files:
            first = files.list_files(max_entries=10, max_scanned_entries=100)
            second = files.list_files(max_entries=10, max_scanned_entries=100)

        self.assertEqual(first, ("a.py",))
        self.assertEqual(second, first)
        self.assertEqual(iter_files.call_count, 1)

    def test_dot_root_reuses_the_guarded_workspace_inventory(self) -> None:
        (self.root / "a.py").write_text("a", encoding="utf-8")
        (self.root / ".chaos-agent").mkdir()
        (self.root / ".chaos-agent" / "private.txt").write_text(
            "private", encoding="utf-8"
        )
        files = self.files()

        with patch.object(
            files, "_iter_files", wraps=files._iter_files
        ) as iter_files:
            first = files.list_files(
                ".", max_entries=10, max_scanned_entries=100
            )
            second = files.list_files(
                max_entries=10, max_scanned_entries=100
            )

        self.assertEqual(first, ("a.py",))
        self.assertEqual(second, first)
        self.assertEqual(iter_files.call_count, 1)

    def test_inventory_invalidation_refreshes_a_cached_root_listing(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("a", encoding="utf-8")
        files = self.files()

        self.assertEqual(
            files.list_files(max_entries=10, max_scanned_entries=100),
            ("src/a.py",),
        )
        (self.root / "src" / "b.py").write_text("b", encoding="utf-8")
        self.assertEqual(
            files.list_files(max_entries=10, max_scanned_entries=100),
            ("src/a.py",),
        )

        files.invalidate_inventory()

        self.assertEqual(
            files.list_files(max_entries=10, max_scanned_entries=100),
            ("src/a.py", "src/b.py"),
        )

    def test_inventory_invalidation_does_not_wait_for_active_enumeration(self) -> None:
        files = self.files()
        scan_started = threading.Event()
        release_scan = threading.Event()
        invalidated = threading.Event()
        calls = 0

        def generated() -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                scan_started.set()
                release_scan.wait(2)
            yield "a.py"

        with patch.object(files, "_iter_files", side_effect=lambda _: generated()):
            listed: list[tuple[str, ...]] = []
            listing = threading.Thread(
                target=lambda: listed.append(
                    files.list_files(
                        max_entries=10,
                        max_scanned_entries=100,
                    )
                )
            )
            listing.start()
            self.assertTrue(scan_started.wait(1))
            invalidator = threading.Thread(
                target=lambda: (
                    files.invalidate_inventory(),
                    invalidated.set(),
                )
            )
            invalidator.start()
            try:
                self.assertTrue(invalidated.wait(0.5))
            finally:
                release_scan.set()
            listing.join(timeout=1)
            invalidator.join(timeout=1)

            refreshed = files.list_files(
                max_entries=10,
                max_scanned_entries=100,
            )

        self.assertEqual(listed, [("a.py",)])
        self.assertEqual(refreshed, ("a.py",))
        self.assertEqual(calls, 2)

    def test_inventory_cache_evicts_old_budget_variants(self) -> None:
        (self.root / "a.py").write_text("a", encoding="utf-8")
        files = self.files()

        with patch.object(
            files, "_iter_files", wraps=files._iter_files
        ) as iter_files:
            for max_entries in range(1, MAX_INVENTORY_CACHE_ENTRIES + 2):
                files.list_files(
                    max_entries=max_entries,
                    max_scanned_entries=100,
                )
            calls_after_distinct_budgets = iter_files.call_count
            files.list_files(max_entries=1, max_scanned_entries=100)

        self.assertEqual(
            calls_after_distinct_budgets,
            MAX_INVENTORY_CACHE_ENTRIES + 1,
        )
        self.assertEqual(
            iter_files.call_count,
            calls_after_distinct_budgets + 1,
        )

    def test_scan_budget_stops_flat_directory_at_budget_plus_one(self) -> None:
        for index in range(10):
            (self.root / f"file-{index}.txt").write_text("x", encoding="utf-8")
        real_scandir = os.scandir
        consumed: list[str] = []

        def counting_scandir(path: object) -> CountingScandir:
            return CountingScandir(real_scandir(path), consumed)  # type: ignore[arg-type]

        with patch(
            "code_agent.workspace._file_walk.os.scandir",
            side_effect=counting_scandir,
        ):
            with self.assertRaises(WorkspaceError) as raised:
                self.files().list_files(
                    max_entries=1, max_scanned_entries=3
                )
        self.assertEqual(type(raised.exception).__name__, "WorkspaceScanLimitError")
        self.assertEqual(len(consumed), 4)

    def test_max_entries_stops_consuming_ordered_generator_immediately(self) -> None:
        for sort_results in (True, False):
            with self.subTest(sorted=sort_results):
                consumed: list[str] = []

                def generated() -> object:
                    for path in ("a.py", "b.py", "c.py", "d.py"):
                        consumed.append(path)
                        yield path
                files = self.files()
                with patch.object(files, "_iter_files", return_value=generated()):
                    listed = files.list_files(
                        sorted=sort_results, max_entries=2
                    )

                self.assertEqual(listed, ("a.py", "b.py"))
                self.assertEqual(consumed, ["a.py", "b.py"])

    def test_sorted_walk_is_globally_ordered_across_directories(self) -> None:
        (self.root / "a").mkdir()
        (self.root / "a" / "z.py").write_text("z", encoding="utf-8")
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "b.py").write_text("b", encoding="utf-8")

        self.assertEqual(
            self.files().list_files(), ("a.txt", "a/z.py", "b.py")
        )

    def test_lists_sorted_relative_files_with_limit_and_negation(self) -> None:
        (self.root / ".gitignore").write_text(
            "generated/*\n!generated/keep.py\n*.tmp\n", encoding="utf-8"
        )
        (self.root / "generated").mkdir()
        (self.root / "generated" / "drop.py").write_text("drop", encoding="utf-8")
        (self.root / "generated" / "keep.py").write_text("keep", encoding="utf-8")
        (self.root / "z.py").write_text("z", encoding="utf-8")
        (self.root / "a.py").write_text("a", encoding="utf-8")
        (self.root / "noise.tmp").write_text("noise", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("hidden", encoding="utf-8")

        listed = self.files().list_files(sorted=True, max_entries=3)

        self.assertEqual(listed, (".gitignore", "a.py", "generated/keep.py"))

    def test_skips_a_directory_link_that_escapes_when_supported(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir(exist_ok=True)
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        self.addCleanup(lambda: (outside / "secret.txt").unlink(missing_ok=True))
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")

        self.assertEqual(self.files().list_files(), ())


if __name__ == "__main__":
    unittest.main()
