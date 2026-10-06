"""Children inherit an immutable authority without finalizing the parent task."""
import unittest

from code_agent.core.action_execution import ActionLineage
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.models import ActionRequest, ActionResult, ToolCall
from code_agent.core.task import TaskAuthorization


class CaptureDispatcher:
    async def dispatch(self, request, cancellation, authorization=None, *, execution_context=None):
        self.authorization, self.context = authorization, execution_context
        return ActionResult(request.id, request.name, "ok")


class ChildAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_dispatch_uses_parent_authority_and_actual_child_origin(self):
        authorization = TaskAuthorization("C:/isolated", allow_workspace_write=False)
        dispatcher = CaptureDispatcher()
        engine = AgentEngine(None, None, dispatcher, None,
            action_lineage=ActionLineage("parent", "parent-task", "delegate-call"),
            inherited_authorization=authorization)
        call = ToolCall("read-call", "read_file", {"path": "a.txt"})
        context = engine._action_execution_context("child", call.id, None)
        result = await engine._invoke_action(ActionRequest(call.id, call.name, call.arguments),
            call, CancellationToken(), None, context)
        self.assertFalse(result.is_error)
        self.assertIs(dispatcher.authorization, authorization)
        self.assertEqual(dispatcher.context.owner_thread_id, "parent")
        self.assertEqual(dispatcher.context.origin_thread_id, "child")
        self.assertEqual(dispatcher.context.task_id, "parent-task")
        self.assertEqual(dispatcher.context.parent_request_id, "delegate-call")

    def test_inherited_authority_requires_concrete_parent_task(self):
        authorization = TaskAuthorization("C:/isolated")
        for lineage in (None, ActionLineage("parent")):
            with self.assertRaises(ValueError):
                AgentEngine(None, None, None, None, action_lineage=lineage,
                    inherited_authorization=authorization)
        with self.assertRaises(TypeError):
            AgentEngine(None, None, None, None, action_lineage=ActionLineage("parent", "task"),
                inherited_authorization={"workspace_root": "C:/isolated"})
