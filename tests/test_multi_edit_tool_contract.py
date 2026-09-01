from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.tools import tool_definitions, validate_tool_arguments  # noqa: E402


_DIGEST = "a" * 64


class MultiEditToolSchemaTests(unittest.TestCase):
    def test_plan_and_apply_have_strict_nested_schemas(self) -> None:
        schemas = {
            tool.name: tool.to_dict()["parameters"] for tool in tool_definitions()
        }

        plan = schemas["plan_workspace_edits_v1"]
        apply = schemas["apply_workspace_edit_plan_v1"]
        operations = plan["properties"]["operations"]
        item = operations["items"]

        self.assertEqual(plan["required"], ["operations"])
        self.assertEqual(operations["maxItems"], 32)
        self.assertEqual(item["type"], "object")
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(
            item["properties"]["kind"]["enum"],
            ["write", "replace", "delete", "move"],
        )
        self.assertEqual(apply["required"], ["plan_id", "plan_digest"])
        self.assertEqual(apply["properties"]["plan_digest"]["pattern"], "^[0-9a-f]{64}$")
        self.assertNotIn("confirmed", apply["properties"])
        self.assertNotIn("overwrite", apply["properties"])

    def test_each_operation_kind_accepts_only_its_exact_fields(self) -> None:
        valid = (
            {"kind": "write", "path": "empty.txt", "content": ""},
            {
                "kind": "replace",
                "path": "a.py",
                "old_text": "old",
                "new_text": "",
            },
            {"kind": "delete", "path": "old.py"},
            {
                "kind": "move",
                "source_path": "old.py",
                "destination_path": "pkg/new.py",
            },
        )
        for index, operation in enumerate(valid):
            with self.subTest(operation=operation["kind"]):
                self.assertIsNone(
                    validate_tool_arguments(
                        "plan_workspace_edits_v1", {"operations": [operation]}
                    )
                )
                unexpected = dict(operation, unexpected=index)
                self.assertIsNotNone(
                    validate_tool_arguments(
                        "plan_workspace_edits_v1", {"operations": [unexpected]}
                    )
                )

    def test_invalid_operation_shapes_and_oversized_batches_are_rejected(self) -> None:
        invalid = (
            {"kind": "write", "path": "a.py"},
            {"kind": "replace", "path": "a.py", "old_text": "", "new_text": "x"},
            {"kind": "delete", "path": ""},
            {"kind": "move", "source_path": "a.py", "destination_path": ""},
            {"kind": "unknown", "path": "a.py"},
        )
        for operation in invalid:
            with self.subTest(operation=operation):
                self.assertIsNotNone(
                    validate_tool_arguments(
                        "plan_workspace_edits_v1", {"operations": [operation]}
                    )
                )
        too_many = [
            {"kind": "delete", "path": f"old-{index}.py"} for index in range(33)
        ]
        self.assertIsNotNone(
            validate_tool_arguments(
                "plan_workspace_edits_v1", {"operations": too_many}
            )
        )

    def test_apply_requires_only_a_nonempty_id_and_lowercase_digest(self) -> None:
        self.assertIsNone(
            validate_tool_arguments(
                "apply_workspace_edit_plan_v1",
                {"plan_id": "plan-1", "plan_digest": _DIGEST},
            )
        )
        invalid = (
            {"plan_id": "", "plan_digest": _DIGEST},
            {"plan_id": "plan-1", "plan_digest": _DIGEST.upper()},
            {"plan_id": "plan-1", "plan_digest": "a" * 63},
            {"plan_id": "plan-1", "plan_digest": _DIGEST, "confirmed": True},
            {"plan_id": "plan-1", "plan_digest": _DIGEST, "overwrite": False},
            {
                "plan_id": "plan-1",
                "plan_digest": _DIGEST,
                "edit_plan": {"combined_diff": "forged"},
            },
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(
                    validate_tool_arguments(
                        "apply_workspace_edit_plan_v1", arguments
                    )
                )


if __name__ == "__main__":
    unittest.main()
