from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import (  # noqa: E402
    BinaryFileError,
    FileTooLargeError,
    WorkspaceError,
)
import code_agent.workspace.files as files_module  # noqa: E402
from code_agent.workspace.files import (  # noqa: E402
    MAX_INVENTORY_CACHE_ENTRIES,
    WorkspaceFiles,
)
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class CountingScandir:
    def __init__(self, iterator: object, consumed: list[str]) -> None:
        self.iterator = iterator
        self.consumed = consumed

    def __enter__(self) -> "CountingScandir":
        self.iterator.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, *args: object) -> None:
        self.iterator.__exit__(*args)  # type: ignore[attr-defined]

    def __iter__(self) -> "CountingScandir":
        return self

    def __next__(self) -> os.DirEntry[str]:
        entry = next(self.iterator)  # type: ignore[arg-type]
        self.consumed.append(entry.name)
        return entry


class WorkspaceFilesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def files(self) -> WorkspaceFiles:
        return WorkspaceFiles(self.guard, IgnoreRules.from_workspace(self.root))


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


class ReadTextTests(WorkspaceFilesTestCase):
    def test_reads_utf8_bom_and_an_inclusive_line_range(self) -> None:
        target = self.root / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbfone\ntwo\nthree\n")

        document = self.files().read_text(target, start_line=2, end_line=3)

        self.assertEqual(document.relative_path, "bom.txt")
        self.assertEqual(document.text, "two\nthree\n")
        self.assertEqual(document.total_lines, 3)
        self.assertEqual((document.start_line, document.end_line), (2, 3))
        with self.assertRaises(FrozenInstanceError):
            document.text = "changed"  # type: ignore[misc]

    def test_empty_file_has_zero_total_lines(self) -> None:
        (self.root / "empty.txt").write_bytes(b"")

        document = self.files().read_text("empty.txt")

        self.assertEqual(document.text, "")
        self.assertEqual(document.total_lines, 0)
        self.assertEqual((document.start_line, document.end_line), (1, 0))

    def test_rejects_nul_invalid_utf8_and_oversized_files(self) -> None:
        (self.root / "nul.bin").write_bytes(b"hello\0world")
        (self.root / "invalid.bin").write_bytes(b"\xff\xfe")
        (self.root / "large.txt").write_bytes(b"12345")

        for path in ("nul.bin", "invalid.bin"):
            with self.subTest(path=path):
                with self.assertRaises(BinaryFileError):
                    self.files().read_text(path)
        with self.assertRaises(FileTooLargeError):
            self.files().read_text("large.txt", max_bytes=4)
        self.assertEqual(
            self.files().read_text("large.txt", max_bytes=5).text, "12345"
        )

    def test_rejects_invalid_line_ranges(self) -> None:
        (self.root / "lines.txt").write_text("one\ntwo\n", encoding="utf-8")

        ranges = ((0, None), (2, 1), (3, None), (1, 3))
        for start, end in ranges:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    self.files().read_text(
                        "lines.txt", start_line=start, end_line=end
                    )


class SearchTests(WorkspaceFilesTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (self.root / "a.py").write_text(
            "Needle and needle\nnumber 123\n", encoding="utf-8"
        )
        (self.root / "b.txt").write_text("needle in text\n", encoding="utf-8")
        (self.root / "ignored.txt").write_text("needle hidden\n", encoding="utf-8")
        (self.root / "binary.py").write_bytes(b"needle\0hidden")

    def test_literal_search_is_case_insensitive_and_reports_each_match(self) -> None:
        matches = self.files().search("needle", include_globs=("*.py",))

        self.assertEqual(
            [(match.path, match.line, match.column, match.text) for match in matches],
            [
                ("a.py", 1, 1, "Needle and needle"),
                ("a.py", 1, 12, "Needle and needle"),
            ],
        )

    def test_regex_case_sensitive_glob_and_result_limit(self) -> None:
        matches = self.files().search(
            r"[A-Z][a-z]+|\d+",
            regex=True,
            case_sensitive=True,
            include_globs=("*.py",),
            max_results=2,
        )

        self.assertEqual([(item.text, item.column) for item in matches], [
            ("Needle and needle", 1),
            ("number 123", 8),
        ])

    def test_search_skips_ignored_and_binary_files(self) -> None:
        matches = self.files().search("needle")

        self.assertEqual([match.path for match in matches], ["a.py", "a.py", "b.txt"])

    def test_invalid_regex_and_limits_are_explicit_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid regular expression"):
            self.files().search("(", regex=True)
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.files().search("needle", max_results=limit)

    def test_search_timeout_configuration_and_pattern_length_are_bounded(self) -> None:
        self.assertEqual(self.files().search_timeout_s, 2.0)
        for invalid in (0, -1, float("inf"), True, "2"):
            with self.subTest(search_timeout_s=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    WorkspaceFiles(
                        self.guard,
                        IgnoreRules.from_workspace(self.root),
                        search_timeout_s=invalid,  # type: ignore[arg-type]
                    )

        with self.assertRaises(ValueError):
            self.files().search("x" * 10_001)

    def test_catastrophic_regex_raises_structured_timeout(self) -> None:
        (self.root / "slow.txt").write_text(
            "a" * 20_000 + "!", encoding="utf-8"
        )
        files = WorkspaceFiles(
            self.guard,
            IgnoreRules.from_workspace(self.root),
            search_timeout_s=0.001,
        )

        with self.assertRaises(WorkspaceError) as raised:
            files.search(r"(a+)+$", regex=True)

        self.assertEqual(type(raised.exception).__name__, "SearchTimeoutError")

    def test_literal_search_uses_the_same_global_deadline(self) -> None:
        with patch.object(files_module.time, "monotonic", side_effect=(0.0, 3.0)):
            with self.assertRaises(WorkspaceError) as raised:
                self.files().search("needle")

        self.assertEqual(type(raised.exception).__name__, "SearchTimeoutError")

    def test_search_deadline_is_checked_inside_directory_enumeration(self) -> None:
        for index in range(5):
            (self.root / f"scan-{index}.txt").write_text(
                "needle", encoding="utf-8"
            )
        real_scandir = os.scandir
        consumed: list[str] = []
        clock_values = iter((0.0, 0.1, 3.0))
        def counting_scandir(path: object) -> CountingScandir:
            return CountingScandir(real_scandir(path), consumed)  # type: ignore[arg-type]
        def clock() -> float:
            return next(clock_values, 3.0)
        with patch(
            "code_agent.workspace._file_walk.os.scandir",
            side_effect=counting_scandir,
        ), patch.object(files_module.time, "monotonic", side_effect=clock):
            with self.assertRaises(WorkspaceError) as raised:
                self.files().search("needle")
        self.assertEqual(type(raised.exception).__name__, "SearchTimeoutError")
        self.assertEqual(len(consumed), 2)


if __name__ == "__main__":
    unittest.main()
