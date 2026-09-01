from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.action_execution import (  # noqa: E402
    ActionExecutionContext,
    ActionLineage,
)
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.errors import SessionPersistenceError  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionResult,
    ModelEvent,
    ModelEventKind,
    ToolCall,
)
from code_agent.core.task import (  # noqa: E402
    TaskAuthorization,
    TaskContract,
    TaskRecord,
    TaskStatus,
)
from code_agent.core.task_supervisor import (  # noqa: E402
    SupervisionDecision,
    SupervisionKind,
)
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def completed() -> ModelEvent:
    return ModelEvent(ModelEventKind.COMPLETED)


class FailingActionStartedSessions(MemorySessionRepository):
    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        if event.kind is EventKind.ACTION_STARTED:
            raise RuntimeError("injected persistence failure")
        await super().append_event(thread_id, event)


class PausingSupervisor:
    def before_external_action(self) -> SupervisionDecision:
        return SupervisionDecision(SupervisionKind.PAUSE, "injected pause")


class ContextSpyEngine(AgentEngine):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.context_builds = 0

    def _action_execution_context(
        self,
        thread_id: str,
        request_id: str,
        task: TaskRecord | None,
    ) -> ActionExecutionContext:
        self.context_builds += 1
        return super()._action_execution_context(thread_id, request_id, task)

    async def _pause_task(
        self,
        thread_id: str,
        task: TaskRecord,
        supervisor: object,
        reason: str,
    ) -> None:
        return None


class AgentEngineActionLineageTests(unittest.IsolatedAsyncioTestCase):
    def test_constructor_rejects_untyped_lineage(self) -> None:
        with self.assertRaisesRegex(
            TypeError, "action_lineage must be an ActionLineage or None"
        ):
            AgentEngine(
                FakeModelClient(()),
                FakeContextBuilder(),
                FakeActionDispatcher(),
                MemorySessionRepository(),
                action_lineage="parent",  # type: ignore[arg-type]
            )

    async def test_root_task_context_uses_current_task_id(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})
        result = ActionResult("call-1", "read_file", {"text": "contents"})
        actions = FakeActionDispatcher((result,))
        engine = AgentEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        task = TaskRecord(
            "task-1",
            thread_id,
            TaskContract(
                "inspect", TaskAuthorization.local_workspace("C:/repo")
            ),
            TaskStatus.RUNNING,
        )

        _ = [
            event
            async for event in engine._dispatch(
                thread_id,
                call,
                CancellationToken(),
                is_available=True,
                task=task,
            )
        ]

        self.assertEqual(
            actions.contexts,
            [
                ActionExecutionContext(
                    thread_id, thread_id, "call-1", task_id="task-1"
                )
            ],
        )
        self.assertEqual(actions.authorizations, [task.contract.authorization])

    async def test_child_context_preserves_parent_lineage(self) -> None:
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})
        model = FakeModelClient(
            (
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), completed()),
                (completed(),),
            )
        )
        actions = FakeActionDispatcher(
            (ActionResult("call-1", "read_file", {"text": "contents"}),)
        )
        sessions = MemorySessionRepository()
        child_thread = await sessions.create_thread()
        engine = AgentEngine(
            model,
            FakeContextBuilder(),
            actions,
            sessions,
            action_lineage=ActionLineage(
                "parent-thread", "parent-task", "delegate-request"
            ),
        )

        _ = [
            event
            async for event in engine.run("inspect", thread_id=child_thread)
        ]

        self.assertEqual(
            actions.contexts,
            [
                ActionExecutionContext(
                    "parent-thread",
                    child_thread,
                    "call-1",
                    "parent-task",
                    "delegate-request",
                )
            ],
        )

    async def test_supervisor_pauses_edit_plan_apply_before_dispatch(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        actions = FakeActionDispatcher()
        engine = ContextSpyEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        task = TaskRecord(
            "task-1",
            thread_id,
            TaskContract(
                "write", TaskAuthorization.local_workspace("C:/repo")
            ),
            TaskStatus.RUNNING,
        )
        call = ToolCall(
            "call-1",
            "apply_workspace_edit_plan_v1",
            {"plan_id": "plan", "plan_digest": "a" * 64},
        )

        events = [
            event
            async for event in engine._dispatch(
                thread_id,
                call,
                CancellationToken(),
                is_available=True,
                task=task,
                supervisor=PausingSupervisor(),  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(events[-1].kind, EventKind.TASK_PAUSED)
        self.assertNotIn(EventKind.ACTION_STARTED, [e.kind for e in events])
        self.assertEqual(engine.context_builds, 0)
        self.assertEqual(actions.requests, [])
        self.assertEqual(actions.contexts, [])

    async def test_started_persistence_precedes_context_construction(self) -> None:
        sessions = FailingActionStartedSessions()
        thread_id = await sessions.create_thread()
        actions = FakeActionDispatcher(
            (ActionResult("call-1", "read_file", {"text": "unused"}),)
        )
        engine = ContextSpyEngine(
            FakeModelClient(()), FakeContextBuilder(), actions, sessions
        )
        call = ToolCall("call-1", "read_file", {"path": "a.txt"})

        with self.assertRaises(SessionPersistenceError):
            _ = [
                event
                async for event in engine._dispatch(
                    thread_id,
                    call,
                    CancellationToken(),
                    is_available=True,
                )
            ]

        self.assertEqual(engine.context_builds, 0)
        self.assertEqual(actions.requests, [])
        self.assertEqual(actions.contexts, [])


if __name__ == "__main__":
    unittest.main()
