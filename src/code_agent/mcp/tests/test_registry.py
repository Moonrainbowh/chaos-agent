from __future__ import annotations
import unittest
from code_agent.mcp.registry import McpController, McpPolicyBridge, McpRegistry, McpRisk, McpServer, McpTool

class McpRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_only_namespaces_configured_servers(self) -> None:
        server = McpServer("docs", "python", ("server.py",), enabled=True, approved=True, tools=(McpTool("search", "Search docs", {"type": "object"}, McpRisk.READ),))
        registry = McpRegistry((server,))
        self.assertEqual(registry.namespace("docs", "search"), "mcp.docs.search")
        request = McpPolicyBridge(registry).request("call-1", "docs", "search", {"query": "x"})
        self.assertEqual(request.name, "mcp.docs.search")
        with self.assertRaises(ValueError): registry.namespace("missing", "search")

    def test_disabled_or_unmapped_tools_are_not_exposed(self) -> None:
        registry = McpRegistry((McpServer("docs", "python", enabled=False, approved=True, tools=(McpTool("search", "Search", {"type": "object"}, McpRisk.READ),)),))
        self.assertEqual(registry.definitions(), ())
        with self.assertRaises(ValueError): registry.namespace("docs", "search")

    async def test_controller_exposes_tools_only_after_enable(self) -> None:
        class Manager:
            async def start(self, _: McpServer): return (McpTool("search", "Search", {"type": "object"}, McpRisk.READ),)
            async def close(self, _: str): return None
            async def aclose(self): return None
        server = McpServer("docs", "python", enabled=True, approved=True, tool_risks={"search": McpRisk.READ})
        control = McpController(McpRegistry((server,)), Manager())
        self.assertEqual(control.definitions(), ())
        await control.enable("docs")
        self.assertEqual(control.definitions()[0].name, "mcp.docs.search")

if __name__ == "__main__": unittest.main()
