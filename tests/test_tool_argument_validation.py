from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.tools import validate_tool_arguments  # noqa: E402


class ToolArgumentValidationTests(unittest.TestCase):
    def test_run_command_valid_arguments(self):
        err = validate_tool_arguments("run_command", {"command": "Get-ChildItem"})
        self.assertIsNone(err)

    def test_run_command_unexpected_arguments_gives_actionable_error(self):
        err = validate_tool_arguments(
            "run_command",
            {"command": "Get-ChildItem", "extra_bad": "foo"},
        )
        self.assertIsNotNone(err)
        self.assertIn("unexpected argument(s): extra_bad", err)
        self.assertIn("Allowed arguments:", err)
        self.assertIn("command", err)

    def test_read_file_unexpected_argument_lists_fields(self):
        err = validate_tool_arguments(
            "read_file",
            {"path": "a.txt", "bogus": "value"},
        )
        self.assertIsNotNone(err)
        self.assertIn("unexpected argument(s): bogus", err)
        self.assertIn("Allowed arguments: encoding, path", err)

    def test_run_command_with_description_is_valid(self):
        err = validate_tool_arguments(
            "run_command",
            {"command": "Get-ChildItem", "description": "Run directory listing"},
        )
        self.assertIsNone(err)

    def test_run_command_unexpected_arguments_still_rejected(self):
        err = validate_tool_arguments(
            "run_command",
            {"command": "Get-ChildItem", "description": "Listing", "unknown_key": "val"},
        )
        self.assertIsNotNone(err)
        self.assertIn("unexpected argument(s): unknown_key", err)
        self.assertIn("command", err)
        self.assertIn("description", err)


if __name__ == "__main__":
    unittest.main()
