from __future__ import annotations

import unittest

from code_agent.capabilities.catalog import (
    CapabilityStrategy,
    CONTRACT_TOOL_NAME,
    contract_result,
    disclosed_name,
    progressive_tools,
    tool_definition_digest,
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

    def test_progressive_initial_projection_contains_only_directory_loader(self) -> None:
        projected = progressive_tools(
            (self.loader, self.read, self.run),
            (),
            strategy=CapabilityStrategy.PROGRESSIVE,
        )

        self.assertEqual([tool.name for tool in projected], [CONTRACT_TOOL_NAME])
        self.assertIn("read_file [read]", projected[0].description)
        self.assertIn("run_command [execute]", projected[0].description)
        self.assertEqual(
            projected[0].parameters["properties"]["name"]["enum"],
            ("read_file", "run_command"),
        )

    def test_loaded_contract_is_available_on_later_projection(self) -> None:
        projected = progressive_tools(
            (self.loader, self.read, self.run),
            ("read_file", "removed_tool"),
            strategy=CapabilityStrategy.PROGRESSIVE,
        )

        self.assertEqual(
            [tool.name for tool in projected],
            [CONTRACT_TOOL_NAME, "read_file"],
        )

    def test_hybrid_preloads_only_explicit_builtin_read_allowlist(self) -> None:
        code = _tool("read_code_slices")
        mcp = _tool("mcp.docs.read_file")
        plugin = _tool("plugin.read_file")

        projected = progressive_tools(
            (self.loader, self.read, code, self.run, mcp, plugin), ()
        )

        self.assertEqual(
            [tool.name for tool in projected],
            [CONTRACT_TOOL_NAME, "read_file", "read_code_slices"],
        )

    def test_legacy_exposes_the_complete_snapshot(self) -> None:
        tools = (self.loader, self.read, self.run)

        projected = progressive_tools(
            tools, (), strategy=CapabilityStrategy.LEGACY
        )

        self.assertEqual(projected, (self.read, self.run))

    def test_contract_result_returns_only_short_confirmation(self) -> None:
        result = contract_result(
            ActionRequest("call-1", CONTRACT_TOOL_NAME, {"name": "read_file"}),
            (self.loader, self.read),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(
            set(result.output), {"name", "digest", "availability"}
        )
        self.assertEqual(result.output["name"], "read_file")
        self.assertEqual(result.output["digest"], tool_definition_digest(self.read))
        self.assertEqual(result.output["availability"], "next_model_turn")
        self.assertEqual(disclosed_name(result), "read_file")
        self.assertNotIn("schema", repr(result.output).casefold())
        self.assertNotIn("description", repr(result.output).casefold())

    def test_definition_digest_is_stable_and_schema_sensitive(self) -> None:
        same = _tool("read_file", "Read a file with a long description.")
        changed = ToolDefinition(
            self.read.name,
            self.read.description,
            {"type": "object", "properties": {"path": {"type": "string"}}},
        )

        self.assertEqual(
            tool_definition_digest(self.read), tool_definition_digest(same)
        )
        self.assertNotEqual(
            tool_definition_digest(self.read), tool_definition_digest(changed)
        )

    def test_unknown_contract_fails_without_disclosing_a_tool(self) -> None:
        result = contract_result(
            ActionRequest("call-2", CONTRACT_TOOL_NAME, {"name": "missing"}),
            (self.loader, self.read),
        )

        self.assertTrue(result.is_error)
        self.assertIsNone(disclosed_name(result))


if __name__ == "__main__":
    unittest.main()
