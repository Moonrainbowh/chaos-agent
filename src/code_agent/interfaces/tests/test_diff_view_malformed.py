from __future__ import annotations

import unittest

from code_agent.interfaces.diff_view import (
    DiffController,
    DiffScope,
    DiffSourceDocument,
    DiffView,
)


def unified(path: str) -> str:
    return (
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )


class StaticSource:
    def __init__(self, result: object) -> None:
        self.result = result

    async def read_diff(self, paths: tuple[str, ...] = ()) -> object:
        return self.result


class MalformedParserTests(unittest.TestCase):
    def test_overconsumed_hunk_is_dropped_and_explicit_next_file_resyncs(self) -> None:
        patch = (
            "diff --git a/bad.txt b/bad.txt\n"
            "--- a/bad.txt\n+++ b/bad.txt\n"
            "@@ -1 +1 @@\n-old\n-extra-old\n"
            "diff --git a/good.txt b/good.txt\n"
            "--- a/good.txt\n+++ b/good.txt\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )

        view = DiffView.parse(patch, DiffScope.UNSTAGED, True)

        self.assertEqual(tuple(file.path for file in view.files), ("good.txt",))

    def test_unfinished_hunk_at_eof_is_not_a_trusted_file_diff(self) -> None:
        patch = (
            "diff --git a/incomplete.txt b/incomplete.txt\n"
            "--- a/incomplete.txt\n+++ b/incomplete.txt\n"
            "@@ -2,2 +2,2 @@\n-old\n+new\n"
        )

        view = DiffView.parse(patch, DiffScope.UNSTAGED, True)

        self.assertEqual(view.files, ())

    def test_invalid_quoted_octal_paths_are_skipped_without_blocking_resync(self) -> None:
        for escaped in (r"\400", r"\377"):
            with self.subTest(escaped=escaped):
                patch = (
                    f'diff --git "a/bad{escaped}.txt" "b/bad{escaped}.txt"\n'
                    f'--- "a/bad{escaped}.txt"\n'
                    f'+++ "b/bad{escaped}.txt"\n'
                    "@@ -1 +1 @@\n-old\n+new\n"
                    "diff --git a/good.txt b/good.txt\n"
                    "--- a/good.txt\n+++ b/good.txt\n"
                    "@@ -1 +1 @@\n-old\n+new\n"
                )

                view = DiffView.parse(patch, DiffScope.UNSTAGED, True)

                self.assertEqual(tuple(file.path for file in view.files), ("good.txt",))


class MalformedControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonempty_unparseable_live_diff_falls_back_to_recorded(self) -> None:
        live = DiffSourceDocument(DiffScope.UNSTAGED, "not a unified diff\n", True)

        view = await DiffController(StaticSource(live)).load(
            DiffScope.UNSTAGED, unified("recorded.py")
        )

        self.assertEqual(view.current.path, "recorded.py")  # type: ignore[union-attr]
        self.assertFalse(view.current.fresh)  # type: ignore[union-attr]
        self.assertTrue(view.stale)
        self.assertIn("recorded · stale", view.render()[0].text)


class DiffViewInputValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = DiffView.parse(unified("file.py"))

    def test_comment_rejects_non_integer_and_negative_line_indices(self) -> None:
        for line_index in (True, 0.5, "0", -1):
            with self.subTest(line_index=line_index):
                with self.assertRaises(ValueError):
                    self.view.comment(line_index, "note")  # type: ignore[arg-type]

    def test_render_rejects_invalid_limits_but_zero_reports_hidden_lines(self) -> None:
        for max_lines in (True, 0.5, -1):
            with self.subTest(max_lines=max_lines):
                with self.assertRaises(ValueError):
                    self.view.render(max_lines=max_lines)  # type: ignore[arg-type]

        rendered = self.view.render(max_lines=0)
        self.assertIn("lines hidden", rendered[-1].text)


if __name__ == "__main__":
    unittest.main()
