from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from code_agent.interfaces.diff_view import (
    DiffController,
    DiffScope,
    DiffSourceDocument,
    DiffView,
)


def unified(path: str, old: str = "old", new: str = "new") -> str:
    return (
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1 @@\n-{old}\n+{new}\n"
    )


class RichSource:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    async def read_diff(self, paths: tuple[str, ...] = ()) -> object:
        self.calls.append(paths)
        return self.result


class FailingSource:
    async def read_diff(self, paths: tuple[str, ...] = ()) -> object:
        raise AssertionError(f"recorded scope consulted live source: {paths}")


class DiffViewDocumentTests(unittest.TestCase):
    def test_metadata_only_empty_and_binary_documents_create_file_diffs(self) -> None:
        empty = (
            'diff --git "a/empty file.txt" "b/empty file.txt"\n'
            "new file mode 100644\n"
            "index 0000000..e69de29\n"
        )
        binary = (
            'diff --git "a/caf\\303\\251 space.bin" "b/caf\\303\\251 space.bin"\n'
            "new file mode 100644\n"
            'Binary files /dev/null and "b/caf\\303\\251 space.bin" differ\n'
        )

        view = DiffView.from_documents(
            (
                DiffSourceDocument(DiffScope.UNTRACKED, empty, True),
                DiffSourceDocument(DiffScope.UNTRACKED, binary, False),
            )
        )

        self.assertEqual(tuple(file.path for file in view.files), ("empty file.txt", "café space.bin"))
        self.assertEqual(tuple(file.scope for file in view.files), (DiffScope.UNTRACKED,) * 2)
        self.assertEqual(tuple(file.fresh for file in view.files), (True, False))

    def test_quoted_normal_patch_decodes_octal_and_space_path(self) -> None:
        patch = (
            'diff --git "a/caf\\303\\251 space.txt" "b/caf\\303\\251 space.txt"\n'
            '--- "a/caf\\303\\251 space.txt"\n'
            '+++ "b/caf\\303\\251 space.txt"\n'
            "@@ -1 +1 @@\n-old\n+new\n"
        )

        view = DiffView.parse(patch, DiffScope.UNSTAGED, True)

        self.assertEqual(view.current.path, "café space.txt")  # type: ignore[union-attr]
        self.assertEqual((view.current.scope, view.current.fresh), (DiffScope.UNSTAGED, True))  # type: ignore[union-attr]

    def test_scope_values_and_frozen_document_contract(self) -> None:
        self.assertEqual(
            tuple(scope.value for scope in DiffScope),
            (
                "working-tree",
                "staged",
                "unstaged",
                "untracked",
                "per-turn",
                "since-checkpoint",
            ),
        )
        document = DiffSourceDocument(DiffScope.STAGED, unified("same.py"), True)
        with self.assertRaises(FrozenInstanceError):
            document.fresh = False  # type: ignore[misc]

    def test_duplicate_paths_across_scopes_keep_comments_distinct(self) -> None:
        documents = (
            DiffSourceDocument(DiffScope.STAGED, unified("same.py"), True),
            DiffSourceDocument(DiffScope.UNSTAGED, unified("same.py", "new", "newer"), True),
        )

        view = DiffView.from_documents(documents)

        self.assertEqual(tuple(file.path for file in view.files), ("same.py", "same.py"))
        self.assertEqual(
            tuple(file.scope for file in view.files),
            (DiffScope.STAGED, DiffScope.UNSTAGED),
        )
        first = view.comment(1, "staged note")
        view.move_file(1)
        second = view.comment(1, "unstaged note")
        self.assertEqual((first.scope, second.scope), (DiffScope.STAGED, DiffScope.UNSTAGED))
        self.assertEqual((first.path, second.path), ("same.py", "same.py"))

    def test_navigation_filter_statistics_and_render_limit_remain_bounded(self) -> None:
        view = DiffView.from_documents(
            (
                DiffSourceDocument(DiffScope.STAGED, unified("a.py"), True),
                DiffSourceDocument(DiffScope.UNTRACKED, unified("b.py", "", "added"), True),
            )
        )

        self.assertEqual((view.current.additions, view.current.removals), (1, 1))  # type: ignore[union-attr]
        view.move_file(-1)
        self.assertEqual(view.current.path, "b.py")  # type: ignore[union-attr]
        view.filter("A.PY")
        self.assertEqual(view.current.path, "a.py")  # type: ignore[union-attr]
        self.assertIn("lines hidden", view.render(max_lines=1)[-1].text)


class DiffControllerScopeTests(unittest.IsolatedAsyncioTestCase):
    def documents(self) -> tuple[DiffSourceDocument, ...]:
        return (
            DiffSourceDocument(DiffScope.STAGED, unified("staged.py"), True),
            DiffSourceDocument(DiffScope.UNSTAGED, unified("unstaged.py"), True),
            DiffSourceDocument(DiffScope.UNTRACKED, unified("untracked.py"), True),
        )

    async def test_working_tree_aggregates_all_three_live_facets(self) -> None:
        source = RichSource(self.documents())

        view = await DiffController(source).load(DiffScope.WORKING_TREE, None, ("src",))

        self.assertEqual(source.calls, [("src",)])
        self.assertEqual(
            tuple(file.scope for file in view.files),
            (DiffScope.STAGED, DiffScope.UNSTAGED, DiffScope.UNTRACKED),
        )
        self.assertEqual(
            tuple(file.path for file in view.files),
            ("staged.py", "unstaged.py", "untracked.py"),
        )

    async def test_explicit_live_scope_selects_only_matching_document(self) -> None:
        for scope in (DiffScope.STAGED, DiffScope.UNSTAGED, DiffScope.UNTRACKED):
            with self.subTest(scope=scope):
                view = await DiffController(RichSource(self.documents())).load(scope, None)
                self.assertEqual(tuple(file.scope for file in view.files), (scope,))

    async def test_recorded_scopes_never_consult_live_source(self) -> None:
        controller = DiffController(FailingSource())
        for scope in (DiffScope.PER_TURN, DiffScope.SINCE_CHECKPOINT):
            with self.subTest(scope=scope):
                view = await controller.load(scope, unified("recorded.py"))
                self.assertEqual(view.current.scope, scope)  # type: ignore[union-attr]
                self.assertFalse(view.current.fresh)  # type: ignore[union-attr]
                self.assertIn("recorded", view.render()[0].text)

    async def test_crlf_equivalent_live_diff_is_fresh_not_stale(self) -> None:
        recorded = unified("same.py").replace("\n", "\r\n")
        source = RichSource(DiffSourceDocument(DiffScope.UNSTAGED, unified("same.py"), True))

        view = await DiffController(source).load(DiffScope.UNSTAGED, recorded)

        header = view.render()[0].text
        self.assertIn("unstaged · fresh", header)
        self.assertNotIn("stale", header)

    async def test_live_recorded_disagreement_is_visible_as_stale(self) -> None:
        source = RichSource(
            DiffSourceDocument(DiffScope.UNSTAGED, unified("live.py", "old", "live"), True)
        )

        view = await DiffController(source).load(
            DiffScope.UNSTAGED, unified("recorded.py", "old", "recorded")
        )

        self.assertEqual(view.current.path, "live.py")  # type: ignore[union-attr]
        self.assertIn("unstaged · fresh · stale", view.render()[0].text)

    async def test_empty_live_diff_falls_back_to_stale_recorded(self) -> None:
        source = RichSource(DiffSourceDocument(DiffScope.UNSTAGED, "", True))

        view = await DiffController(source).load(DiffScope.UNSTAGED, unified("recorded.py"))

        self.assertEqual(view.current.path, "recorded.py")  # type: ignore[union-attr]
        self.assertFalse(view.current.fresh)  # type: ignore[union-attr]
        self.assertIn("unstaged · recorded · stale", view.render()[0].text)

    async def test_legacy_string_source_is_fresh_unstaged_working_tree(self) -> None:
        source = RichSource(unified("legacy.py"))

        view = await DiffController(source).load(DiffScope.WORKING_TREE, None)

        self.assertEqual(view.current.scope, DiffScope.UNSTAGED)  # type: ignore[union-attr]
        self.assertTrue(view.current.fresh)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
