from __future__ import annotations

import unittest

from code_agent.core.models import ToolDefinition

from code_agent.skills.capabilities import check_capabilities
from code_agent.skills.registry import SkillManifest


def _skill(*requires: str) -> SkillManifest:
    return SkillManifest("salt", "salt analysis", "test", "digest", "text", True, (), requires)


class CapabilityCheckTests(unittest.TestCase):
    def test_reports_missing_without_authorizing_or_mutating_tools(self) -> None:
        tools = (ToolDefinition("read_workspace", "read", {"type": "object"}),)
        result = check_capabilities(_skill("read_workspace", "salt_simulator"), tools)
        self.assertEqual(result.available, ("read_workspace",))
        self.assertEqual(result.missing, ("salt_simulator",))
        self.assertFalse(result.satisfied)
        self.assertEqual(tuple(tool.name for tool in tools), ("read_workspace",))

    def test_aliases_are_explicit_and_do_not_create_new_tools(self) -> None:
        tools = (ToolDefinition("mcp.salt.salt_simulator", "simulate", {"type": "object"}),)
        result = check_capabilities(
            _skill("salt_simulator"), tools,
            aliases={"salt_simulator": "mcp.salt.salt_simulator"},
        )
        self.assertTrue(result.satisfied)
        self.assertEqual(result.available, ("salt_simulator",))

    def test_duplicate_requirements_are_deterministically_collapsed(self) -> None:
        result = check_capabilities(_skill("read_workspace", "read_workspace"), ())
        self.assertEqual(result.required, ("read_workspace",))
        self.assertEqual(result.missing, ("read_workspace",))

    def test_rejects_non_host_tool_objects(self) -> None:
        with self.assertRaises(TypeError):
            check_capabilities(_skill("read_workspace"), (object(),))


if __name__ == "__main__":
    unittest.main()
