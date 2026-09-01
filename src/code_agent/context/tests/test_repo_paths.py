from __future__ import annotations

import unittest

from code_agent.context.repo_paths import (
    canonical_path_key,
    canonical_repo_path,
    logical_lines,
)


class RepoPathTests(unittest.TestCase):
    def test_canonical_path_accepts_only_workspace_relative_posix_form(self) -> None:
        self.assertEqual(canonical_repo_path("src/pkg/module.py"), "src/pkg/module.py")

        for invalid in (
            "",
            ".",
            "../module.py",
            "src/../module.py",
            "src//pkg/module.py",
            "src/pkg/module.py/",
            "src\\module.py",
            "C:/repo/module.py",
            "//server/share/module.py",
            "src/\0module.py",
        ):
            with self.subTest(path=invalid), self.assertRaises((TypeError, ValueError)):
                canonical_repo_path(invalid)

    def test_windows_key_casefolds_without_changing_display_path(self) -> None:
        display = canonical_repo_path("Src/Pkg/Module.py")

        self.assertEqual(display, "Src/Pkg/Module.py")
        self.assertEqual(
            canonical_path_key(display, case_insensitive=True),
            canonical_path_key("src/pkg/module.py", case_insensitive=True),
        )
        self.assertNotEqual(
            canonical_path_key(display, case_insensitive=False),
            canonical_path_key("src/pkg/module.py", case_insensitive=False),
        )

    def test_logical_lines_make_crlf_and_lf_ranges_identical(self) -> None:
        self.assertEqual(
            logical_lines("alpha\r\nbeta\r\ngamma\r\n"),
            logical_lines("alpha\nbeta\ngamma\n"),
        )
        self.assertEqual(logical_lines("alpha\rbeta"), ("alpha", "beta"))
        self.assertEqual(logical_lines(""), ())


if __name__ == "__main__":
    unittest.main()
