from __future__ import annotations

import unittest

from code_agent.capabilities.catalog import (
    CONTRACT_TOOL_NAME,
    contract_result,
    disclosed_name,
    progressive_tools,
)
from code_agent.core.models import ActionRequest, ToolDefinition


def _tool(name: str, description: str = "Use the tool.") -> ToolDefinition:
    return ToolDefinition(
        name,
        description,
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )


class ProgressiveToolCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = _tool(CONTRACT_TOOL_NAME, "Load one contract.")
        self.read = _tool("read_file", "Read a file with a long description.")
        self.run = _tool("run_command", "Run a command.")

    def test_initial_projection_contains_only_directory_loader(self) -> None:
        projected = progressive_tools((self.loader, self.read, self.run), ())

        self.assertEqual([tool.name for tool in projected], [CONTRACT_TOOL_NAME])
        self.assertIn("read_file [read]", projected[0].description)
        self.assertIn("run_command [execute]", projected[0].description)
        self.assertEqual(
            projected[0].parameters["properties"]["name"]["enum"],
            ("read_file", "run_command"),
        )

    def test_loaded_contract_is_available_on_later_projection(self) -> None:
        projected = progressive_tools(
            (self.loader, self.read, self.run), ("read_file", "removed_tool")
        )

        self.assertEqual(
            [tool.name for tool in projected],
            [CONTRACT_TOOL_NAME, "read_file"],
        )

    def test_contract_result_returns_current_schema_and_disclosure_metadata(self) -> None:
        result = contract_result(
            ActionRequest("call-1", CONTRACT_TOOL_NAME, {"name": "read_file"}),
            (self.loader, self.read),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(
            result.to_dict()["output"]["contract"], self.read.to_dict()
        )
        self.assertEqual(result.output["availability"], "next_model_turn")
        self.assertEqual(disclosed_name(result), "read_file")

    def test_unknown_contract_fails_without_disclosing_a_tool(self) -> None:
        result = contract_result(
            ActionRequest("call-2", CONTRACT_TOOL_NAME, {"name": "missing"}),
            (self.loader, self.read),
        )

        self.assertTrue(result.is_error)
        self.assertIsNone(disclosed_name(result))


if __name__ == "__main__":
    unittest.main()
