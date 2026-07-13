from __future__ import annotations
import unittest
from code_agent.mcp.registry import McpRegistry, McpServer

class McpRegistryTests(unittest.TestCase):
    def test_registry_only_namespaces_configured_servers(self) -> None:
        registry = McpRegistry((McpServer("docs", "http", "https://example.test/mcp"),))
        self.assertEqual(registry.namespace("docs", "search"), "mcp.docs.search")
        with self.assertRaises(ValueError): registry.namespace("missing", "search")

if __name__ == "__main__": unittest.main()
