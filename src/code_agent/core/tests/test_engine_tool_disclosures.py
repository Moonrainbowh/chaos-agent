from __future__ import annotations

import unittest

from code_agent.capabilities.catalog import (
    CapabilityStrategy,
    CONTRACT_TOOL_NAME,
    contract_result,
    tool_definition_digest,
)
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
)
from code_agent.core.tests._engine_support import (
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def _tool(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(name, description, {"type": "object"})


class _ReloadingDispatcher(FakeActionDispatcher):
    def __init__(
        self, loader: ToolDefinition, current: ToolDefinition, changed: ToolDefinition
    ) -> None:
        super().__init__()
        self._tools = (loader, current)
        self._changed = changed

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization=None,
        *,
        execution_context=None,
    ):
        del cancellation, task_authorization, execution_context
        self.requests.append(request)
        result = contract_result(request, self._tools)
        self._tools = (self._tools[0], self._changed)
        return result


class EngineToolDisclosureTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_is_disclosed_only_after_loading_current_digest(self) -> None:
        load = ToolCall("load-1", CONTRACT_TOOL_NAME, {"name": "read_file"})
        read = ToolCall("read-1", "read_file", {"path": "a.txt"})
        completed = ModelEvent(ModelEventKind.COMPLETED)
        model = FakeModelClient(
            (
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=load), completed),
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=read), completed),
                (ModelEvent(ModelEventKind.TEXT_DELTA, text="done"), completed),
            )
        )
        loader = _tool(CONTRACT_TOOL_NAME, "Load a contract")
        definition = _tool("read_file", "Read a file")
        actions = FakeActionDispatcher(
            (
                ActionResult(
                    "load-1",
                    CONTRACT_TOOL_NAME,
                    {
                        "name": "read_file",
                        "digest": tool_definition_digest(definition),
                        "availability": "next_model_turn",
                    },
                    metadata={"disclosed_tool": "read_file"},
                ),
                ActionResult("read-1", "read_file", {"text": "contents"}),
            )
        )
        actions._tools = (loader, definition)

        _ = [
            event
            async for event in AgentEngine(
                model,
                FakeContextBuilder(),
                actions,
                MemorySessionRepository(),
                capability_strategy=CapabilityStrategy.PROGRESSIVE,
            ).run("inspect")
        ]

        self.assertEqual(
            [tool.name for tool in model.calls[0][2]], [CONTRACT_TOOL_NAME]
        )
        self.assertEqual(
            [tool.name for tool in model.calls[1][2]],
            [CONTRACT_TOOL_NAME, "read_file"],
        )
        self.assertEqual(
            actions.requests,
            [
                ActionRequest("load-1", CONTRACT_TOOL_NAME, {"name": "read_file"}),
                ActionRequest("read-1", "read_file", {"path": "a.txt"}),
            ],
        )

    async def test_extension_reload_with_changed_schema_revokes_disclosure(self) -> None:
        for name in ("mcp.docs.lookup", "plugin.docs.lookup"):
            with self.subTest(name=name):
                loader = _tool(CONTRACT_TOOL_NAME, "Load a contract")
                current = _tool(name, "Current schema")
                changed = _tool(name, "Changed schema")
                actions = _ReloadingDispatcher(loader, current, changed)
                model = FakeModelClient(
                    (
                        (
                            ModelEvent(
                                ModelEventKind.TOOL_CALL,
                                tool_call=ToolCall("load-1", CONTRACT_TOOL_NAME, {"name": name}),
                            ),
                            ModelEvent(ModelEventKind.COMPLETED),
                        ),
                        (ModelEvent(ModelEventKind.COMPLETED),),
                    )
                )

                events = [
                    event
                    async for event in AgentEngine(
                        model,
                        FakeContextBuilder(),
                        actions,
                        MemorySessionRepository(),
                        capability_strategy=CapabilityStrategy.PROGRESSIVE,
                    ).run("inspect")
                ]

                self.assertEqual(events[-1].kind, EventKind.COMPLETED)
                self.assertEqual(
                    [tool.name for tool in model.calls[1][2]],
                    [CONTRACT_TOOL_NAME],
                )


if __name__ == "__main__":
    unittest.main()
