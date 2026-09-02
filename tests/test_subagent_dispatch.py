from __future__ import annotations

import unittest

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent_win.subagents import RestrictedDispatcher


class RestrictedDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_delegation_is_visible_only_when_explicitly_enabled(self) -> None:
        class Inner:
            def tools(self):
                return (
                    ToolDefinition(
                        "load_tool_contract", "Load.", {"type": "object"}
                    ),
                    ToolDefinition("read_file", "Read.", {"type": "object"}),
                    ToolDefinition(
                        "delegate_agent", "Delegate.", {"type": "object"}
                    ),
                    ToolDefinition("list_agents", "List.", {"type": "object"}),
                    ToolDefinition("send_message", "Send.", {"type": "object"}),
                    ToolDefinition("rename_agent", "Rename.", {"type": "object"}),
                )

            async def dispatch(
                self,
                request,
                cancellation,
                task_authorization=None,
                *,
                execution_context=None,
            ):
                return ActionResult(request.id, request.name, {"ok": True})

        child = RestrictedDispatcher(
            Inner(),
            (
                "read_file",
                "delegate_agent",
                "list_agents",
                "send_message",
                "rename_agent",
            ),
        )
        team_main = RestrictedDispatcher(
            Inner(),
            (
                "read_file",
                "delegate_agent",
                "list_agents",
                "send_message",
                "rename_agent",
            ),
            allow_delegation=True,
            allow_coordination=True,
        )

        self.assertEqual(
            tuple(tool.name for tool in child.tools()),
            ("load_tool_contract", "read_file"),
        )
        self.assertEqual(
            tuple(tool.name for tool in team_main.tools()),
            (
                "load_tool_contract",
                "read_file",
                "delegate_agent",
                "list_agents",
                "send_message",
            ),
        )
        rejected = await child.dispatch(
            ActionRequest("child", "delegate_agent", {}), CancellationToken()
        )
        accepted = await team_main.dispatch(
            ActionRequest("main", "delegate_agent", {}), CancellationToken()
        )
        hidden_contract = await child.dispatch(
            ActionRequest(
                "contract", "load_tool_contract", {"name": "delegate_agent"}
            ),
            CancellationToken(),
        )
        self.assertTrue(rejected.is_error)
        self.assertFalse(accepted.is_error)
        self.assertTrue(hidden_contract.is_error)

    async def test_restricted_dispatcher_forwards_execution_context_by_keyword(
        self,
    ) -> None:
        class Inner:
            def tools(self):
                return ()

            async def dispatch(
                self,
                request,
                cancellation,
                task_authorization=None,
                *,
                execution_context=None,
            ):
                self.context = execution_context
                return ActionResult(request.id, request.name, {})

        inner = Inner()
        restricted = RestrictedDispatcher(inner, ("read_file",))
        context = ActionExecutionContext("owner", "origin", "call-1")

        result = await restricted.dispatch(
            ActionRequest("call-1", "read_file", {"path": "note.txt"}),
            CancellationToken(),
            execution_context=context,
        )

        self.assertFalse(result.is_error)
        self.assertIs(inner.context, context)


if __name__ == "__main__":
    unittest.main()
