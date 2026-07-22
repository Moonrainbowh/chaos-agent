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
        self.cancelled: list[str] = []
        self.closed: list[str] = []

    async def start(self, server: McpServer) -> tuple[McpTool, ...]:
        return (
            McpTool("search", "Search", {"type": "object"}, McpRisk.READ),
        )

    async def cancel(self, name: str) -> None:
        self.cancelled.append(name)

    async def close(self, name: str) -> None:
        self.closed.append(name)

    async def aclose(self) -> None:
        return None

    def health(self, name: str) -> object:
        return {"server": name, "healthy": name not in self.closed}


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
                        enabled=True,
                        approved=True,
                        tool_risks={"search": McpRisk.READ},
                    ),
                )
            ),
            manager,
            risks,
        )

        await controller.enable("docs")
        self.assertEqual(risks["delegate_agent"], "write")
        self.assertEqual(risks["mcp.docs.search"], "read")

        await controller.disable("docs")
        self.assertEqual(risks, {"delegate_agent": "write"})

    async def test_generation_changes_only_after_published_lifecycle_state(self) -> None:
        manager = Manager()
        controller = McpController(
            McpRegistry(
                (
                    McpServer(
                        "docs",
                        "python",
                        enabled=True,
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
        self.assertEqual(enabled.tools[0].name, "mcp.docs.search")
        self.assertEqual(disabled.generation, enabled.generation + 1)
        self.assertEqual(disabled.tools, ())
        self.assertEqual(manager.cancelled, ["docs"])
        self.assertEqual(manager.closed, ["docs"])
        self.assertEqual(controller.diagnose("docs")["server"], "docs")


if __name__ == "__main__":
    unittest.main()
