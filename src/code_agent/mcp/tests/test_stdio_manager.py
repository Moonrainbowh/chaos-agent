from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.mcp.registry import McpRisk, McpServer, McpTool
from code_agent.mcp.stdio_manager import McpSdkAdapter, StdioMcpManager
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter


class _Adapter(McpSdkAdapter):
    def __init__(self) -> None: self.started = self.cancelled = self.closed = False
    async def start(self, _: McpServer) -> None: self.started = True
    async def list_tools(self) -> tuple[McpTool, ...]: return (McpTool("read", "Read", {"type": "object"}, McpRisk.READ),)
    async def call(self, name: str, arguments: object) -> object: return {"name": name, "arguments": arguments}
    async def cancel(self) -> None: self.cancelled = True
    async def close(self) -> None: self.closed = True


class StdioMcpManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_is_bounded_and_cleanly_closed(self) -> None:
        adapter = _Adapter(); server = McpServer("docs", "python", enabled=True, approved=True)
        manager = StdioMcpManager(lambda _: adapter)
        tools = await manager.start(server)
        result = await manager.call(server, "read", {"path": "README.md"})
        await manager.cancel("docs"); await manager.close("docs")

        self.assertEqual(tools[0].name, "read")
        self.assertEqual(result["name"], "read")
        self.assertTrue(adapter.started and adapter.cancelled and adapter.closed)

    async def test_unapproved_server_never_starts(self) -> None:
        manager = StdioMcpManager(lambda _: _Adapter())
        with self.assertRaises(PermissionError): await manager.start(McpServer("docs", "python"))

    async def test_official_adapter_completes_real_stdio_lifecycle(self) -> None:
        fixture = Path(__file__).with_name("sdk_fixture_server.py")
        server = McpServer("fixture", sys.executable, (str(fixture),), enabled=True, approved=True, tool_risks={"read_text": McpRisk.READ})
        manager = StdioMcpManager(lambda item: OfficialMcpSdkAdapter(tool_risks=item.tool_risks))

        tools = await manager.start(server)
        result = await manager.call(server, "read_text", {"path": "README.md"})
        await manager.cancel("fixture")
        await manager.aclose()

        self.assertEqual(tools[0].name, "read_text")
        self.assertIn("fixture:README.md", str(result))
