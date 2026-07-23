from __future__ import annotations

import unittest

from code_agent.mcp.registry import (
    McpController,
    McpRegistry,
    McpRisk,
    McpServer,
    McpTool,
)


class Manager:
    def __init__(self) -> None:
        self.started: list[McpServer] = []
        self.active: set[str] = set()
        self.cancelled: list[str] = []
        self.closed: list[str] = []

    async def start(self, server: McpServer) -> tuple[McpTool, ...]:
        if not server.enabled or not server.approved:
            raise PermissionError("MCP server is not approved and enabled")
        self.started.append(server)
        self.active.add(server.name)
        return (
            McpTool("search", "Search", {"type": "object"}, McpRisk.READ),
        )

    async def cancel(self, name: str) -> None:
        self.cancelled.append(name)

    async def close(self, name: str) -> None:
        self.closed.append(name)
        self.active.discard(name)

    async def aclose(self) -> None:
        return None

    def health(self, name: str) -> object:
        return {"server": name, "healthy": name in self.active}


class McpSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_risk_sync_preserves_non_mcp_policy_entries(self) -> None:
        risks = {"delegate_agent": "write"}
        manager = Manager()
        controller = McpController(
            McpRegistry(
                (
                    McpServer(
                        "docs",
                        "python",
                        enabled=False,
                        approved=True,
                        tool_risks={"search": McpRisk.READ},
                    ),
                )
            ),
            manager,
            risks,
        )

        await controller.enable("docs")
        self.assertTrue(controller.status("docs")[0].enabled)
        self.assertEqual(manager.active, {"docs"})
        self.assertEqual(risks["delegate_agent"], "write")
        self.assertEqual(risks["mcp.docs.search"], "read")

        await controller.disable("docs")
        self.assertFalse(controller.status("docs")[0].enabled)
        self.assertEqual(controller.definitions(), ())
        self.assertEqual(manager.active, set())
        self.assertEqual(risks, {"delegate_agent": "write"})

    async def test_generation_changes_only_after_published_lifecycle_state(self) -> None:
        manager = Manager()
        controller = McpController(
            McpRegistry(
                (
                    McpServer(
                        "docs",
                        "python",
                        enabled=False,
                        approved=True,
                        tool_risks={"search": McpRisk.READ},
                    ),
                )
            ),
            manager,
        )
        initial = controller.snapshot()

        await controller.enable("docs")
        enabled = controller.snapshot()
        await controller.disable("docs")
        disabled = controller.snapshot()

        self.assertEqual(initial.tools, ())
        self.assertEqual(enabled.generation, initial.generation + 1)
        self.assertTrue(enabled.servers[0].enabled)
        self.assertEqual(enabled.tools[0].name, "mcp.docs.search")
        self.assertEqual(disabled.generation, enabled.generation + 1)
        self.assertFalse(disabled.servers[0].enabled)
        self.assertEqual(disabled.tools, ())
        self.assertTrue(manager.started[0].enabled)
        self.assertEqual(manager.cancelled, ["docs"])
        self.assertEqual(manager.closed, ["docs"])
        self.assertEqual(manager.active, set())
        self.assertEqual(controller.diagnose("docs")["server"], "docs")

    async def test_restart_republishes_enabled_state_and_live_tools(self) -> None:
        manager = Manager()
        controller = McpController(
            McpRegistry(
                (
                    McpServer(
                        "docs",
                        "python",
                        enabled=False,
                        approved=True,
                        tool_risks={"search": McpRisk.READ},
                    ),
                )
            ),
            manager,
        )
        await controller.enable("docs")
        before = controller.snapshot()

        tools = await controller.restart("docs")
        restarted = controller.snapshot()

        self.assertEqual(tuple(tool.name for tool in tools), ("search",))
        self.assertTrue(restarted.servers[0].enabled)
        self.assertEqual(
            tuple(tool.name for tool in restarted.tools), ("mcp.docs.search",)
        )
        self.assertEqual(restarted.generation, before.generation + 2)
        self.assertEqual(manager.active, {"docs"})
        self.assertEqual(
            [(server.name, server.enabled) for server in manager.started],
            [("docs", True), ("docs", True)],
        )
        self.assertEqual(manager.cancelled, ["docs"])
        self.assertEqual(manager.closed, ["docs"])


if __name__ == "__main__":
    unittest.main()
