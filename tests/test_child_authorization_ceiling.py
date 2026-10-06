"""Frozen child capability ceilings precede access settings and command rules."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.policy.command_rules import ProcessRuleStore
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode, DecisionOutcome
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.restricted_dispatcher import RestrictedDispatcher
from chaos_agent.tools import tool_definitions


class ChildAuthorizationCeilingTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_and_compact_write_execute_denied_in_all_permissive_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            authorization = TaskAuthorization(str(root), allow_workspace_write=False,
                                                allow_local_execute=False)
            for mode in (ApprovalMode.AUTO, ApprovalMode.FULL_LOCAL, ApprovalMode.UNRESTRICTED):
                with self.subTest(mode=mode):
                    guard = WorkspacePathGuard(root)
                    runtime = SimpleNamespace(run=AsyncMock())
                    inner = RootActionDispatcher(
                        WorkspaceFiles(guard, IgnoreRules.from_workspace(root)), WorkspaceEditor(guard),
                        ActionPolicy(PolicyConfig(mode, workspace_root=root)), ApprovalBroker(), runtime=runtime)
                    dispatcher = RestrictedDispatcher(inner, ("write_file", "run_process_v1"),
                        compact_tools=True, frozen_authorization=authorization)
                    for name, arguments in (
                        ("write_file", {"path": "bypass.txt", "content": "bad"}),
                        ("write", {"operation": "file", "path": "bypass.txt", "content": "bad"}),
                        ("run_process_v1", {"program": sys.executable, "args": ["-V"]}),
                        ("execute", {"operation": "process", "program": sys.executable, "args": ["-V"]}),
                    ):
                        with self.subTest(name=name):
                            result = await dispatcher.dispatch(ActionRequest("ceiling", name, arguments),
                                                               CancellationToken(), authorization)
                            self.assertTrue(result.is_error)
                            self.assertIn("frozen parent authorization", result.output["error"])
                            self.assertEqual(result.name, name)
                    runtime.run.assert_not_awaited()
                    self.assertFalse((root / "bypass.txt").exists())

    async def test_permanent_process_rule_cannot_expand_child_execute_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            authorization = TaskAuthorization(str(root), allow_local_execute=False)
            policy = ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=root))
            guard = WorkspacePathGuard(root)
            runtime = SimpleNamespace(run=AsyncMock())
            rules = ProcessRuleStore(root / "rules.sqlite3")
            rule = rules.allow(sys.executable, ("-V",), workspace_root=root,
                               workspace_fingerprint="child-ceiling")
            request = ActionRequest("rule", "run_process_v1", {"program": sys.executable, "args": ["-V"]})
            self.assertEqual(policy.evaluate(request, authorization,
                permanent_process_rule=rule.id).outcome, DecisionOutcome.ALLOW)
            inner = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)), WorkspaceEditor(guard),
                policy, ApprovalBroker(), runtime=runtime, process_rules=rules,
                workspace_fingerprint="child-ceiling")
            dispatcher = RestrictedDispatcher(inner, ("run_process_v1",), frozen_authorization=authorization)
            result = await dispatcher.dispatch(request, CancellationToken(), authorization)
            self.assertTrue(result.is_error)
            runtime.run.assert_not_awaited()

    async def test_network_outside_and_replaced_authority_refused_before_inner_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            authorization = TaskAuthorization(str(root))
            inner = SimpleNamespace(tools=lambda: tool_definitions(include_web=True),
                dispatch=AsyncMock(return_value=ActionResult("guard", "read_file", {})))
            dispatcher = RestrictedDispatcher(inner, ("web_fetch", "read_file"),
                compact_tools=True, frozen_authorization=authorization)
            for name, arguments in (
                ("web_fetch", {"url": "https://example.test"}),
                ("web", {"operation": "fetch", "url": "https://example.test"}),
                ("read_file", {"path": "../outside.txt"}),
                ("read", {"operation": "file", "path": "../outside.txt"}),
            ):
                with self.subTest(name=name):
                    result = await dispatcher.dispatch(ActionRequest("guard", name, arguments),
                                                       CancellationToken(), authorization)
                    self.assertTrue(result.is_error)
            result = await dispatcher.dispatch(ActionRequest("guard", "read_file", {"path": "note.txt"}),
                CancellationToken(), TaskAuthorization(str(root.parent)))
            self.assertTrue(result.is_error)
            self.assertIn("differs", result.output["error"])
            inner.dispatch.assert_not_awaited()

    async def test_plugin_target_is_classified_against_frozen_child_authority(self):
        from code_agent.core.models import ToolDefinition
        authorization = TaskAuthorization(str(Path.cwd()), allow_workspace_write=False)
        inner = SimpleNamespace(
            tools=lambda: (ToolDefinition("plugin.write", "Write", {"type": "object"}),),
            resolve_supervision_action=lambda request: ActionRequest(request.id, "write_file", request.arguments),
            dispatch=AsyncMock())
        dispatcher = RestrictedDispatcher(inner, ("plugin.write",), frozen_authorization=authorization)
        result = await dispatcher.dispatch(ActionRequest("plugin", "plugin.write", {"path": "x", "content": "bad"}),
                                           CancellationToken(), authorization)
        self.assertTrue(result.is_error)
        inner.dispatch.assert_not_awaited()
