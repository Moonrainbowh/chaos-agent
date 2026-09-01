from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.ignore import IgnoreRules


class IgnoreEncodingTests(unittest.TestCase):
    def test_missing_gitignore_keeps_builtin_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rules = IgnoreRules.from_workspace(Path(temporary))

        self.assertTrue(rules.is_ignored("nested/.git/config"))

    def test_utf8_bom_gitignore_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_bytes(b"\xef\xbb\xbf*.cache\n")

            rules = IgnoreRules.from_workspace(root)

        self.assertTrue(rules.is_ignored("artifact.cache"))

    def test_invalid_utf8_gitignore_fails_instead_of_dropping_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_bytes(b"*.secret\n\xff\n")

            with self.assertRaisesRegex(WorkspaceError, "decode .gitignore"):
                IgnoreRules.from_workspace(root)

    def test_gitignore_read_failure_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("*.cache\n", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(WorkspaceError, "read .gitignore"):
                    IgnoreRules.from_workspace(root)


if __name__ == "__main__":
    unittest.main()
