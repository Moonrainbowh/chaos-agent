"""Actual production composition preserves migrated factory guarantees."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from code_agent.capabilities import CapabilityStrategy
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.core.tests._engine_support import FakeContextBuilder
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest, RunStatus
from chaos_agent.child_runner import EngineChildRunner
from tests.agent_app_test_support import _isolated_application


class WriteClient:
    def __init__(self):
        self.calls = 0
        self.closed = False

    async def stream(self, *args):
        self.calls += 1
        if self.calls == 1:
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall(
                "retained-write", "write_file", {"path": "bypass.txt", "content": "changed"}))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Done.")
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        self.closed = True


class ProductionChildFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app, self.root, _ = _isolated_application(Path(self.temporary.name))
        production = self.app.subagents._runner._factory.__self__
        production.capability_strategy = CapabilityStrategy.LEGACY
        self.client = WriteClient()
        self.allocate = Mock(return_value=self.client)
        production._client_factory = self.allocate
        self.factory = production.child_engine
        owner = await self.app.sessions.create_thread()
        self.task = await self.app.sessions.create_task(owner, TaskContract("Read only",
            TaskAuthorization(str(self.root), allow_workspace_write=False, allow_local_execute=False)))
        await self.app.sessions.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.app.sessions.register_task_execution(self.task.id, "retained-owner", 123, 45)
        await self.app.sessions.get_or_create_task_budget(owner, "parent", EngineLimits())
        self.parent = ActionExecutionContext(owner, owner, "delegate", self.task.id)
        self.agent = AgentDefinition("worker", AgentRole.SUBAGENT, self.app.mode,
            "Write", ("write_file",), may_write=True)

    async def asyncTearDown(self):
        await self.app.aclose()
        self.temporary.cleanup()

    async def assert_no_effects(self):
        self.allocate.assert_not_called()
        self.assertEqual(self.client.calls, 0)
        self.assertFalse((self.root / "bypass.txt").exists())
        budget = await self.app.sessions.load_task_budget(self.task.id)
        self.assertEqual(budget.input_tokens + budget.output_tokens, 0)
        self.assertEqual(budget.tool_calls, 0)

    async def test_direct_factory_rejects_missing_or_untyped_parent_before_provider(self):
        taskless = ActionExecutionContext(self.task.thread_id, self.task.thread_id, 'taskless')
        for args in ((self.agent,), (self.agent, self.parent),
                     (self.agent, self.parent, SimpleNamespace(workspace_root=str(self.root))),
                     (self.agent, taskless, self.task.contract.authorization)):
            with self.subTest(args=len(args)):
                with self.assertRaises(ValueError):
                    self.factory(*args)
        await self.assert_no_effects()

    async def test_production_runner_rejects_foreign_parent_task_before_provider(self):
        runner = self.app.subagents._runner
        token = runner.bind_execution_context(ActionExecutionContext('foreign-owner', 'foreign-owner', 'delegate', self.task.id))
        try:
            with self.assertRaises(ValueError):
                await runner.run(ChildRunRequest(self.task.id, 'Write', self.agent, 1, 100000, 3, 30), CancellationToken())
        finally:
            runner.reset_execution_context(token)
        await self.assert_no_effects()

    async def test_production_factory_preserves_readonly_parent_authorization_and_shared_budget(self):
        runner = self.app.subagents._runner
        runner.bind_execution_context(self.parent)
        result = await runner.run(ChildRunRequest(self.task.id, "Write", self.agent,
            1, 100000, 3, 30), CancellationToken())
        self.assertEqual(self.client.calls, 2)
        self.assertTrue(self.client.closed)
        self.assertFalse((self.root / "bypass.txt").exists())
        self.assertEqual(result.usage.total_tokens, 30)
        budget = await self.app.sessions.load_task_budget(self.task.id)
        self.assertEqual(budget.input_tokens + budget.output_tokens, 30)
        self.assertEqual(budget.tool_calls, 1)
        self.assertEqual(budget.model_turns, 2)
