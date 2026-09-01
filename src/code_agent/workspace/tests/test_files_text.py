from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import code_agent.workspace.files as files_module
from code_agent.workspace.errors import (
    BinaryFileError,
    FileTooLargeError,
    WorkspaceError,
)
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules

try:
    from ._files_test_support import CountingScandir, WorkspaceFilesTestCase
except ImportError:
    from _files_test_support import CountingScandir, WorkspaceFilesTestCase


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
        (self.root / "invalid.bin").write_bytes(b"\xff")
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
