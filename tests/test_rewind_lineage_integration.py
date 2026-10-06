from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext, ActionLineage
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.task import TaskAuthorization, TaskStatus
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import (
    ActionRequest,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
)
from code_agent.core.tests._engine_support import (
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    ChildRunRequest,
    RunStatus,
)
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.subagents import (
    EngineChildRunner,
    RestrictedDispatcher,
    SubagentRuntime,
)
from tests.test_subagent_integration import _runtime


class _Engine:
    async def run(self, objective: str, cancellation=None):
        yield AgentEvent(
            EventKind.MESSAGE_ADDED,
            {"message": Message(role="assistant", content=objective).to_dict()},
        )
        yield AgentEvent(EventKind.COMPLETED)


def _agent(*, may_write: bool) -> AgentDefinition:
    registry, profiles = _runtime()
    mode = registry.freeze(AgentMode.MEDIUM, profiles)
    role = AgentRole.SUBAGENT if may_write else AgentRole.REVIEW
    tools = ("read_file", "write_file") if may_write else ("read_file",)
    return AgentDefinition(
        "worker", role, mode, "bounded task", tools, may_write=may_write
    )


def _child_request(*, may_write: bool) -> ChildRunRequest:
    return ChildRunRequest(
        "parent", "inspect", _agent(may_write=may_write), 1, 100, 4, 30, "run"
    )


def _capturing_child_runner(
    root: Path, captured: list[ActionExecutionContext]
) -> EngineChildRunner:
    guard = WorkspacePathGuard(root)
    editor = WorkspaceEditor(guard)

    class Capture:
        async def apply_edit(self, context, request, plan):
            captured.append(context)
            editor.apply(plan)

    dispatcher = RootActionDispatcher(
        WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
        editor,
        ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
        ApprovalBroker(),
        capture=Capture(),
    )

    def factory(agent, parent):
        contract = ToolCall(
            "contract", "load_tool_contract", {"name": "write_file"}
        )
        call = ToolCall(
            "write", "write_file", {"path": "note.txt", "content": "after\n"}
        )
        model = FakeModelClient(
            (
                (
                    ModelEvent(ModelEventKind.TOOL_CALL, tool_call=contract),
                    ModelEvent(ModelEventKind.COMPLETED),
                ),
                (
                    ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call),
                    ModelEvent(ModelEventKind.COMPLETED),
                ),
                (ModelEvent(ModelEventKind.COMPLETED),),
            )
        )
        lineage = ActionLineage(
            parent.owner_thread_id, parent.task_id, parent.request_id
        )
        engine = AgentEngine(
            model, FakeContextBuilder(),
            RestrictedDispatcher(dispatcher, agent.effective_tools),
            MemorySessionRepository(), action_lineage=lineage,
        )
        return engine, None

    return EngineChildRunner(factory)


class RewindLineageTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_write_uses_root_owner_child_origin_task_and_parent_request(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("before\n", encoding="utf-8")
            captured: list[ActionExecutionContext] = []
            runner = _capturing_child_runner(root, captured)
            token = runner.bind_execution_context(
                ActionExecutionContext("root", "main", "delegate", "task")
            )
            try:
                result = await runner.run(
                    _child_request(may_write=True), CancellationToken()
                )
            finally:
                runner.reset_execution_context(token)

            self.assertEqual((root / "note.txt").read_text(), "after\n")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(captured[0].owner_thread_id, "root")
        self.assertEqual(captured[0].task_id, "task")
        self.assertEqual(captured[0].parent_request_id, "delegate")
        self.assertNotEqual(captured[0].origin_thread_id, "main")
        self.assertEqual(captured[0].origin_thread_id, result.references[0].identifier)

    async def test_two_concurrent_children_keep_distinct_parent_request_ids(
        self,
    ) -> None:
        registry, profiles = _runtime()
        seen: list[str] = []
        ready = asyncio.Event()

        class BarrierRunner(EngineChildRunner):
            arrived = 0

            async def run(self, request, cancellation):
                self.arrived += 1
                if self.arrived == 2:
                    ready.set()
                await ready.wait()
                return await super().run(request, cancellation)

        def factory(agent, parent):
            seen.append(parent.request_id)
            return _Engine(), None

        runtime = SubagentRuntime(BarrierRunner(factory), registry, profiles)
        requests = (
            ActionRequest("first", "delegate_agent", {
                "objective": "one", "role": "review"
            }),
            ActionRequest("second", "delegate_agent", {
                "objective": "two", "role": "review"
            }),
        )
        contexts = (
            ActionExecutionContext("root", "main", "first", "task"),
            ActionExecutionContext("root", "main", "second", "task"),
        )

        await asyncio.gather(*(
            runtime.dispatch(
                request, CancellationToken(), execution_context=context
            )
            for request, context in zip(requests, contexts)
        ))

        self.assertCountEqual(seen, ["first", "second"])

    async def test_writable_child_without_lineage_fails_before_dispatch(self) -> None:
        called = False

        def factory(agent, parent):
            nonlocal called
            called = True
            return _Engine(), None

        result = await EngineChildRunner(factory).run(
            _child_request(may_write=True), CancellationToken()
        )

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("lineage", result.error)
        self.assertFalse(called)

    async def test_read_only_child_without_lineage_can_run(self) -> None:
        result = await EngineChildRunner(
            lambda agent, parent: (_Engine(), None)
        ).run(_child_request(may_write=False), CancellationToken())

        self.assertEqual(result.status, RunStatus.COMPLETED)

    async def test_delegate_parent_does_not_record_duplicate_mutation(self) -> None:
        class Capture:
            calls: list[str] = []

            async def apply_edit(self, *args):
                self.calls.append("edit")

            async def record_gap(self, *args):
                self.calls.append("gap")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            guard = WorkspacePathGuard(root)
            registry, profiles = _runtime()
            children = SubagentRuntime(EngineChildRunner(
                lambda agent, parent: (_Engine(), None)), registry, profiles)
            capture = Capture()
            dispatcher = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
                WorkspaceEditor(guard),
                ActionPolicy(PolicyConfig(
                    ApprovalMode.FULL_LOCAL, workspace_root=root,
                    mcp_risks={"delegate_agent": "write"})),
                ApprovalBroker(), capture=capture, subagents=children,
            )
            result = await dispatcher.dispatch(
                ActionRequest("delegate", "delegate_agent", {
                "objective": "inspect", "role": "review"
                }),
                CancellationToken(),
                execution_context=ActionExecutionContext(
                    "root", "main", "delegate"),
            )
            await children.aclose()

        self.assertFalse(result.is_error)
        self.assertEqual(capture.calls, [])

    async def test_production_child_engine_context_and_verification_share_owner_scope(self):
        from tests.agent_app_test_support import _isolated_application
        from tests.test_child_execution_scope import ChildClient
        from code_agent.core.task import TaskContract
        from code_agent.core.limits import EngineLimits
        from chaos_agent.task_verification import TaskScopedVerificationService
        with tempfile.TemporaryDirectory() as temporary:
            app, root, _ = _isolated_application(Path(temporary))
            (root / 'scope.txt').write_text('source', encoding='utf-8')
            sessions = app.sessions
            task = await app.foreground_tasks.start('Read scope')
            owner = task.thread_id
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.register_task_execution(task.id, 'scope-owner', 123, 45)
            await sessions.get_or_create_task_budget(owner, 'parent', EngineLimits())
            factory = app.subagents._runner._factory.__self__
            factory._client_factory = lambda *args, **kwargs: ChildClient()
            parent_token = app.subagents.activate(task.id)
            contexts = []
            original = factory._context_for.for_child
            def context_for(*args, **kwargs):
                contexts.append(args[-1])
                return original(*args, **kwargs)
            try:
                with patch.object(factory._context_for, 'for_child', side_effect=context_for), patch(
                    'chaos_agent.application_context.TaskScopedVerificationService',
                    wraps=TaskScopedVerificationService,
                ) as verification:
                    result = await app.subagents.dispatch(
                        ActionRequest('delegate', 'delegate_agent', {'objective': 'Read scope', 'role': 'review', 'token_budget': 100000}),
                        CancellationToken(), execution_context=ActionExecutionContext(owner, owner, 'delegate', task.id),
                    )
                self.assertFalse(result.is_error, result.to_dict())
                self.assertEqual(len(contexts), 1)
                self.assertIs(verification.call_args.args[0], contexts[0])
                child = next(iter(app.subagents._child_threads.values()))
                self.assertEqual((await sessions.load_thread_relation(child)).parent_thread_id, owner)
                records = await sessions.context_records(child, 'usage')
                self.assertTrue(records)
                self.assertTrue(all(record['budget_owner_thread_id'] == owner for record in records))
            finally:
                app.subagents.reset(parent_token)
                await sessions.release_task_execution(task.id, 'scope-owner')
                await app.aclose()


if __name__ == "__main__":
    unittest.main()
