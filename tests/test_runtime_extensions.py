from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.controller import AgentController
from code_agent.plugins.events import PluginProposal
from code_agent.plugins.models import ActionProposal, PluginRisk
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent.workflows.service import WorkflowService
from code_agent_win.app_ui import IntegratedForegroundTaskController
from code_agent_win.plugin_runtime import PluginProposalActionExecutor


class _Engine:
    async def run(self, *args, **kwargs):
        if False:
            yield None


class _Subagents:
    def activate(self, parent_id):
        return object()

    def reset(self, token):
        return None

    async def release(self, parent_id):
        return None


class RuntimeExtensionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_event_action_passes_two_policy_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "note.txt").write_text("safe", encoding="utf-8")
            guard = WorkspacePathGuard(root)
            policy = Mock(
                wraps=ActionPolicy(
                    PolicyConfig(
                        ApprovalMode.AUTO,
                        workspace_root=root,
                        mcp_risks={
                            "plugin_event.reviewer.after": "read"
                        },
                    )
                )
            )
            dispatcher = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
                WorkspaceEditor(guard),
                policy,
                ApprovalBroker(),
            )
            proposal = PluginProposal(
                "reviewer",
                "a" * 64,
                0,
                "after",
                "completed",
                action=ActionProposal(
                    "read_file",
                    {"path": "note.txt"},
                    PluginRisk.READ,
                ),
            )

            result = await PluginProposalActionExecutor(
                dispatcher
            ).execute(proposal, CancellationToken())

        self.assertFalse(result.is_error)
        self.assertEqual(policy.evaluate.call_count, 2)

    async def test_thread_search_uses_host_caller_identity(self) -> None:
        class Threads:
            async def search(self, caller, query, **kwargs):
                self.seen = (caller, query, kwargs)
                return ()

            async def read(self, caller, anchor):
                raise AssertionError("not used")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guard = WorkspacePathGuard(root)
            threads = Threads()
            dispatcher = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
                WorkspaceEditor(guard),
                ActionPolicy(
                    PolicyConfig(
                        ApprovalMode.AUTO,
                        workspace_root=root,
                        mcp_risks={"search_threads": "read"},
                    )
                ),
                ApprovalBroker(),
                threads=threads,
                caller_thread=lambda: "thread-parent",
            )

            result = await dispatcher.dispatch(
                ActionRequest(
                    "search-history",
                    "search_threads",
                    {"query": "decision", "limit": 3},
                ),
                CancellationToken(),
            )

        self.assertFalse(result.is_error)
        self.assertEqual(threads.seen[:2], ("thread-parent", "decision"))

    async def test_foreground_task_creation_persists_a_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = IntegratedForegroundTaskController(
                AgentController(_Engine()),
                sessions,
                root,
                subagents=_Subagents(),
                workflows=WorkflowService(sessions),
            )

            task = await controller.start("Repair the service")
            snapshot = await sessions.load_workflow_for_task(task.id)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.workflow.root_thread_id, task.thread_id)
        self.assertEqual(snapshot.nodes[0].status.value, "running")


if __name__ == "__main__":
    unittest.main()
