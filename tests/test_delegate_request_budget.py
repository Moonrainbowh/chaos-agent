"""Production task/restricted/root schema admits only bounded child reservations."""
import unittest
from types import SimpleNamespace

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.models import ApprovalMode
from chaos_agent.host_composition import compose_host
from chaos_agent.runtime_extensions import ThreadRuntimeBinding
from chaos_agent.restricted_dispatcher import RestrictedDispatcher
from tests import test_subagent_frozen_budget as fixtures
from tests.test_task_scoped_mutations import _service, _Runtime


class DelegateRequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    parent = fixtures.FrozenSubagentBudgetTests.parent

    async def asyncSetUp(self):
        await fixtures.FrozenSubagentBudgetTests.asyncSetUp(self)
        self.root = self.root.resolve()
        service = _service(self.root)
        composition = compose_host(root=self.root, services=service, workspace_runtime=_Runtime(service),
            runtime_config=SimpleNamespace(approval_mode=ApprovalMode.UNRESTRICTED, mcp_servers=()),
            modes=self.modes, sessions=self.sessions, approvals=ApprovalBroker(), thread_binding=ThreadRuntimeBinding())
        task_dispatcher = composition[0]
        task_dispatcher.subagents = self.runtime
        self.dispatcher = RestrictedDispatcher(task_dispatcher, ('delegate_agent',), allow_delegation=True)

    async def asyncTearDown(self):
        await fixtures.FrozenSubagentBudgetTests.asyncTearDown(self)

    async def dispatch(self, task, tokens, call='delegate'):
        activation = self.runtime.activate(task.id)
        try:
            contract = await self.dispatcher.dispatch(ActionRequest('load', 'load_tool_contract', {'name': 'delegate_agent'}), CancellationToken(), task.contract.authorization)
            self.assertFalse(contract.is_error, contract.output)
            return await self.dispatcher.dispatch(ActionRequest(call, 'delegate_agent', {'role': 'review', 'objective': 'inspect', 'token_budget': tokens, 'tool_budget': 4}), CancellationToken(), task.contract.authorization,
                execution_context=ActionExecutionContext(task.thread_id, task.thread_id, call, task.id))
        finally:
            self.runtime.reset(activation)

    async def test_300k_passes_actual_schema_policy_and_runner(self):
        task = await self.parent()
        result = await self.dispatch(task, 300000)
        self.assertFalse(result.is_error, result.output)
        self.assertEqual(self.started, 1)
        self.assertIsNotNone(self.runtime.child_thread(result.output['run_id']))

    async def test_invalid_amounts_rejected_before_runner(self):
        task = await self.parent()
        for index, value in enumerate((300001, True, 300000.0, 255)):
            with self.subTest(value=value):
                result = await self.dispatch(task, value, str(index))
                self.assertTrue(result.is_error, result.output)
        self.assertEqual(self.started, 0)

    async def test_valid_schema_still_obeys_smaller_frozen_parent(self):
        task = await self.parent(tokens=200000)
        self.assertTrue((await self.dispatch(task, 300000)).is_error)
        self.assertEqual(self.started, 0)
