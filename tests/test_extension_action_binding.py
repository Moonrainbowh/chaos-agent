"""Root policy/approval seam: stale extension generations and schema errors."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ToolDefinition
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from chaos_agent.action_dispatcher import RootActionDispatcher


SCHEMA = {'type': 'object', 'properties': {'value': {'type': 'integer'}},
          'required': ['value'], 'additionalProperties': False}


class Mcp:
    def __init__(self):
        self.generation = 1
        self.calls = []
        self.schema = SCHEMA
        self.result = 'fixed fixture result'

    def definitions(self):
        return (ToolDefinition('mcp.fixture.write', 'Fixture', self.schema),)

    def snapshot(self):
        return SimpleNamespace(generation=self.generation, tools=self.definitions())

    def risks(self):
        return {'mcp.fixture.write': 'write'}

    async def call(self, name, arguments, *, expected_generation=None, before_call=None):
        if expected_generation is not None and expected_generation != self.generation:
            raise RuntimeError('stale MCP generation')
        if before_call is not None:
            before_call()
        self.calls.append((name, dict(arguments)))
        return self.result


class Approvals:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False
        self.requests = 0

    async def request(self, *_args, **_kwargs):
        self.requests += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        return True


class Plugins:
    def __init__(self):
        self.generation = 1
        self.enabled = True

    def targets(self):
        return {'fixture.write': 'mcp.fixture.write'} if self.enabled else {}

    def definitions(self):
        return (ToolDefinition('fixture.write', 'Fixture plugin', SCHEMA),) if self.enabled else ()

    def risk_map(self):
        return {'fixture.write': 'write'} if self.enabled else {}


class ExtensionHostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s13-host-')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_absolute() and self.root.is_dir()
        guard = WorkspacePathGuard(self.root)
        self.mcp, self.approvals, self.plugins = Mcp(), Approvals(), Plugins()
        policy = ActionPolicy(PolicyConfig(approval_mode=ApprovalMode.ASK,
            workspace_root=self.root,
            mcp_risks={'mcp.fixture.write': 'write', 'fixture.write': 'write'}))
        self.dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules()),
            WorkspaceEditor(guard), policy, self.approvals, mcp=self.mcp,
            plugins=self.plugins)
        self.dispatcher.interactive = True

    async def asyncTearDown(self):
        await self.dispatcher.web_access.aclose()
        self.temp.cleanup()

    async def test_mcp_restart_during_approval_does_not_execute_new_definition(self):
        self.approvals.block = True
        pending = asyncio.create_task(self.dispatcher.dispatch(
            ActionRequest('fixed-mcp', 'mcp.fixture.write', {'value': 1}), CancellationToken()))
        await self.approvals.entered.wait()
        self.mcp.generation += 1
        self.approvals.release.set()
        result = await pending
        self.assertTrue(result.is_error)
        self.assertEqual(self.mcp.calls, [])

    async def test_plugin_revoked_during_approval_cannot_execute_old_target(self):
        self.approvals.block = True
        pending = asyncio.create_task(self.dispatcher.dispatch(
            ActionRequest('fixed-plugin', 'fixture.write', {'value': 1}), CancellationToken()))
        await self.approvals.entered.wait()
        self.plugins.enabled = False
        self.plugins.generation += 1
        self.approvals.release.set()
        result = await pending
        self.assertTrue(result.is_error)
        self.assertEqual(self.mcp.calls, [])

    async def test_extension_invalid_schema_is_rejected_before_approval(self):
        result = await self.dispatcher.dispatch(ActionRequest('fixed-invalid',
            'mcp.fixture.write', {'value': True}), CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(self.approvals.requests, 0)
        self.assertEqual(self.mcp.calls, [])

    async def test_current_valid_definition_uses_existing_approval_chain(self):
        result = await self.dispatcher.dispatch(ActionRequest('fixed-valid',
            'mcp.fixture.write', {'value': 1}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertEqual(self.approvals.requests, 1)
        self.assertEqual(len(self.mcp.calls), 1)

    async def test_remote_schema_reference_cannot_be_retrieved_or_invoked(self):
        self.mcp.schema = {'$ref': 'https://fixture.invalid/schema.json'}
        result = await self.dispatcher.dispatch(ActionRequest('fixed-ref',
            'mcp.fixture.write', {'value': 1}), CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(self.approvals.requests, 0)
        self.assertEqual(self.mcp.calls, [])

    async def test_local_defs_and_frozen_nested_arrays_are_validated(self):
        self.mcp.schema = {'type': 'object', '$defs': {'item': {'type': 'integer'}},
            'properties': {'value': {'type': 'array', 'items': {'$ref': '#/$defs/item'}}},
            'required': ['value']}
        invalid = await self.dispatcher.dispatch(ActionRequest('fixed-nested-bad',
            'mcp.fixture.write', {'value': [True]}), CancellationToken())
        valid = await self.dispatcher.dispatch(ActionRequest('fixed-nested-good',
            'mcp.fixture.write', {'value': [1, 2]}), CancellationToken())
        self.assertTrue(invalid.is_error)
        self.assertFalse(valid.is_error)
        self.assertEqual(self.approvals.requests, 1)
        self.assertEqual(len(self.mcp.calls), 1)

    async def test_server_tool_error_does_not_become_host_success(self):
        self.mcp.result = {'isError': True, 'content': [{'type': 'text', 'text': 'fixture failure'}]}
        result = await self.dispatcher.dispatch(ActionRequest('fixed-tool-error',
            'mcp.fixture.write', {'value': 1}), CancellationToken())
        self.assertTrue(result.is_error)
        self.assertTrue(result.output['result']['isError'])
        self.assertEqual(len(self.mcp.calls), 1)

    async def test_definition_change_during_gap_is_rejected_by_controller_generation(self):
        mcp = self.mcp
        class Capture:
            async def record_gap(self, *_args):
                mcp.generation += 1
        self.dispatcher.capture = Capture()
        from code_agent.core.action_execution import ActionExecutionContext
        result = await self.dispatcher.dispatch(ActionRequest('fixed-gap',
            'mcp.fixture.write', {'value': 1}), CancellationToken(),
            execution_context=ActionExecutionContext('owner', 'thread', 'fixed-gap'))
        self.assertTrue(result.is_error)
        self.assertEqual(self.mcp.calls, [])

    async def test_event_tool_failure_is_not_successful_proposal(self):
        from code_agent.plugins.models import ActionProposal, EventSubscription, PluginContributions, PluginManifest, PluginRisk
        from code_agent.plugins.registry import ContributionSnapshot, PluginHost, RegisteredContribution
        from chaos_agent.plugin_runtime import PluginEventCoordinator
        event = EventSubscription('after', ('task_completed',),
            action=ActionProposal('mcp.fixture.write', {'value': 1}, PluginRisk.WRITE))
        manifest = PluginManifest('demo', 'demo', '1.0.0', '1', 'a'*64,
            'fixed-fixture.json', True, True, PluginContributions(events=(event,)))
        registered = RegisteredContribution('demo', 'demo', 'event', 'after', event)
        host = PluginHost(ContributionSnapshot((manifest,), (registered,)))
        self.dispatcher.policy.config.mcp_risks['plugin_event.demo.after'] = 'write'
        self.mcp.result = {'isError': True, 'content': []}
        coordinator = PluginEventCoordinator(host, object(), self.dispatcher)
        outcomes = await coordinator.observe('task_completed', 'fixed-task', {}, CancellationToken())
        self.assertFalse(outcomes[0].ok)
        self.assertEqual(outcomes[0].code, 'host_action_failed')
        self.assertEqual(len(self.mcp.calls), 1)

    async def _queued_plugin_case(self, *, cancel_only=False):
        from code_agent.mcp.registry import McpController, McpRegistry, McpRisk, McpServer, McpTool
        from code_agent.mcp.stdio_manager import McpSdkAdapter, StdioMcpManager
        class Adapter(McpSdkAdapter):
            def __init__(self):
                self.entered, self.release = asyncio.Event(), asyncio.Event()
                self.effects = []
            async def start(self, server): pass
            async def list_tools(self): return (McpTool('write', 'fixture', SCHEMA, McpRisk.WRITE),)
            async def call(self, name, arguments):
                if arguments['value'] == 0:
                    self.entered.set()
                    await self.release.wait()
                self.effects.append(arguments['value'])
                return {'isError': False, 'content': []}
            async def cancel(self): pass
            async def close(self): pass
        adapter = Adapter()
        manager = StdioMcpManager(lambda _: adapter, start_timeout_s=1, call_timeout_s=2, close_timeout_s=1)
        server = McpServer('fixture', 'unused-fixture', approved=True, tool_risks={'write': McpRisk.WRITE})
        controller = McpController(McpRegistry((server,)), manager, self.dispatcher.policy.config.mcp_risks)
        self.dispatcher.mcp = controller
        try:
            await controller.enable('fixture')
            first = asyncio.create_task(controller.call('mcp.fixture.write', {'value': 0}))
            await adapter.entered.wait()
            cancellation = CancellationToken()
            second = asyncio.create_task(self.dispatcher.dispatch(
                ActionRequest('queued-plugin', 'fixture.write', {'value': 1}), cancellation))
            async with asyncio.timeout(1):
                while manager._adapters['fixture'].queue.qsize() != 1:
                    await asyncio.sleep(0)
            if cancel_only:
                cancellation.cancel()
            else:
                self.plugins.enabled = False
                self.plugins.generation += 1
            adapter.release.set()
            await first
            self.assertTrue((await second).is_error)
            self.assertEqual(adapter.effects, [0])
            self.assertTrue(manager.health('fixture').healthy)
            await controller.call('mcp.fixture.write', {'value': 2})
            self.assertEqual(adapter.effects, [0, 2])
        finally:
            adapter.release.set()
            await manager.aclose()

    async def test_queued_plugin_revocation_blocks_effect_without_killing_connection(self):
        await self._queued_plugin_case()

    async def test_queued_token_cancellation_blocks_effect_without_killing_connection(self):
        await self._queued_plugin_case(cancel_only=True)

    async def test_event_revocation_during_source_or_target_approval_blocks_effect(self):
        from code_agent.plugins.models import ActionProposal, EventSubscription, PluginContributions, PluginManifest, PluginRisk
        from code_agent.plugins.registry import ContributionSnapshot, PluginHost, RegisteredContribution
        from chaos_agent.plugin_runtime import PluginEventCoordinator
        event = EventSubscription('after', ('task_completed',),
            action=ActionProposal('mcp.fixture.write', {'value': 1}, PluginRisk.WRITE))
        manifest = PluginManifest('demo', 'demo', '1.0.0', '1', 'a'*64,
            'fixed-fixture.json', True, True, PluginContributions(events=(event,)))
        registered = RegisteredContribution('demo', 'demo', 'event', 'after', event)
        for source_risk in ('write', 'read'):
            with self.subTest(approval=source_risk):
                host = PluginHost(ContributionSnapshot((manifest,), (registered,)))
                self.dispatcher.policy.config.mcp_risks['plugin_event.demo.after'] = source_risk
                self.approvals = Approvals()
                self.approvals.block = True
                self.dispatcher.approvals = self.approvals
                self.mcp.calls.clear()
                coordinator = PluginEventCoordinator(host, object(), self.dispatcher)
                pending = asyncio.create_task(coordinator.observe('task_completed',
                    'fixed-task', {}, CancellationToken()))
                await self.approvals.entered.wait()
                host.revoke('demo')
                self.approvals.release.set()
                outcomes = await pending
                self.assertFalse(outcomes[0].ok)
                self.assertEqual(self.mcp.calls, [])


if __name__ == '__main__':
    unittest.main()
