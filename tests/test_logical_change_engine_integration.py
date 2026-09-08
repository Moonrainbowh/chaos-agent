from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_agent.core.completion_contract import CompletionAssessment, CompletionKind
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.models import ActionRequest, ActionResult, ModelEvent, ModelEventKind, ToolCall, ToolDefinition
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.core.task_state import TaskState, reduce_task_state
from code_agent.core.task_verification import InFlightValidationError, VerificationAssessment
from code_agent.core.tests._engine_support import FakeContextBuilder, FakeModelClient
from code_agent.core.verification_state import VerifierOutcome
from code_agent.context.models import RepoEntry
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.verification.evidence import EvidenceProvenance
from code_agent_win.task_verification import TaskScopedVerificationService


def _completed() -> ModelEvent:
    return ModelEvent(ModelEventKind.COMPLETED)


class WorkspaceDispatcher:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests = []
        self._tools = (
            ToolDefinition("write_file", "Write", {"type": "object"}),
            ToolDefinition("run_verification", "Verify", {"type": "object"}),
        )

    def tools(self):
        return self._tools

    async def dispatch(self, request, cancellation, authorization=None, **kwargs):
        self.requests.append(request)
        if request.name == "write_file":
            target = self.root / request.arguments["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(request.arguments["content"], encoding="utf-8")
            return ActionResult(request.id, request.name, {"path": request.arguments["path"]})
        return ActionResult(
            request.id,
            request.name,
            {"returncode": 0, "stdout": "ok", "kind": request.arguments["kind"]},
        )


class RejectFirstWriteVerification:
    async def prepare(self, task, state):
        return state

    def begin_logical_change(self, task_id):
        return None

    async def commit_logical_change(self, task, state):
        return state, None

    async def record_action(self, task, request, result, state):
        if request.id == "bad":
            raise InFlightValidationError(state, "broken.py:1: invalid syntax")
        return state

    async def suggest_verification(self, task, state):
        return None

    async def assess(self, task, state):
        return VerificationAssessment(
            CompletionAssessment(CompletionKind.VERIFIED, ()),
            VerifierOutcome.PASS,
            state.code_generation,
            state.subject_hash,
        )

    async def finalize(self, task, assessment):
        raise AssertionError("finalize is not needed without a run id")


class LogicalChangeEngineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        container = Path(self.temporary.name).resolve()
        self.root = container / "workspace"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1'\n")
        (self.root / "service.py").write_text("VALUE = 1\n")
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_service.py").write_text("import service\n")
        self.sessions = SQLiteSessionRepository(container / "state.sqlite3")

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def task(self):
        thread_id = await self.sessions.create_thread()
        task = await self.sessions.create_task(
            thread_id,
            TaskContract("modify", TaskAuthorization.local_workspace(str(self.root))),
        )
        return await self.sessions.transition_task(task.id, TaskStatus.RUNNING)

    def service(self) -> TaskScopedVerificationService:
        snapshot = RepoIndexSnapshot(
            7,
            (
                RepoEntry("service.py"),
                RepoEntry("tests/test_service.py", dependencies=("service.py",)),
            ),
        )
        return TaskScopedVerificationService(self.sessions, lambda root: snapshot)

    async def test_turn_commit_runs_graph_targeted_milestone_verifier(self) -> None:
        task = await self.task()
        write = ToolCall("write", "write_file", {"path": "service.py", "content": "VALUE = 2\n"})
        model = FakeModelClient(
            ((ModelEvent(ModelEventKind.TOOL_CALL, tool_call=write), _completed()), (_completed(),))
        )
        dispatcher = WorkspaceDispatcher(self.root)
        events = [
            event async for event in AgentEngine(
                model, FakeContextBuilder(), dispatcher, self.sessions,
                verification=self.service(),
            ).run("change it", thread_id=task.thread_id, task=task)
        ]

        self.assertEqual([item.name for item in dispatcher.requests], ["write_file", "run_verification"])
        self.assertEqual(
            dispatcher.requests[1].arguments["targets"],
            ("tests/test_service.py",),
        )
        self.assertIn(EventKind.COMPLETED, [event.kind for event in events])
        self.assertEqual((await self.sessions.load_task(task.id)).status, TaskStatus.COMPLETED)

    async def test_critical_final_gate_runs_full_tests_then_build(self) -> None:
        task = await self.task()
        write = ToolCall(
            "write", "write_file",
            {"path": "pyproject.toml", "content": "[project]\nname='sample'\nversion='2'\n"},
        )
        model = FakeModelClient(
            ((ModelEvent(ModelEventKind.TOOL_CALL, tool_call=write), _completed()), (_completed(),))
        )
        dispatcher = WorkspaceDispatcher(self.root)
        _ = [
            event async for event in AgentEngine(
                model, FakeContextBuilder(), dispatcher, self.sessions,
                verification=self.service(),
            ).run("change config", thread_id=task.thread_id, task=task)
        ]

        kinds = [item.arguments.get("kind") for item in dispatcher.requests[1:]]
        self.assertEqual(kinds, ["python_unittest", "python_build"])
        self.assertNotIn("targets", dispatcher.requests[1].arguments)
        self.assertEqual((await self.sessions.load_task(task.id)).status, TaskStatus.COMPLETED)

    async def test_l0_failure_blocks_remaining_calls_and_pairs_feedback(self) -> None:
        task = await self.task()
        calls = (
            ToolCall("bad", "write_file", {"path": "broken.py", "content": "def bad(:\n"}),
            ToolCall("later", "write_file", {"path": "later.py", "content": "VALUE = 1\n"}),
        )
        model = FakeModelClient(
            ((*(ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call) for call in calls), _completed()), (_completed(),))
        )
        dispatcher = WorkspaceDispatcher(self.root)
        _ = [
            event async for event in AgentEngine(
                model, FakeContextBuilder(), dispatcher, self.sessions,
                verification=RejectFirstWriteVerification(),
            ).run("write twice", thread_id=task.thread_id, task=task)
        ]

        self.assertEqual([item.id for item in dispatcher.requests], ["bad"])
        messages = await self.sessions.load_messages(task.thread_id)
        feedback = {
            message.tool_call_id: json.loads(message.content)
            for message in messages if message.role == "tool"
        }
        self.assertEqual(feedback["bad"]["output"]["error_code"], "in_flight_validation_failed")
        self.assertEqual(
            feedback["later"]["output"]["error"],
            "blocked after an in-flight validation failure",
        )

    async def test_real_l0_failure_commits_generation_without_verifier(self) -> None:
        task = await self.task()
        service = self.service()
        state = await service.prepare(task, TaskState.empty())
        service.begin_logical_change(task.id)
        action_request = ActionRequest(
            "bad", "write_file", {"path": "broken.py", "content": "def bad(:\n"}
        )
        result = ActionResult("bad", "write_file", {"path": "broken.py"})
        (self.root / "broken.py").write_text("def bad(:\n", encoding="utf-8")
        reduced = reduce_task_state(state, action_request, result)

        with self.assertRaises(InFlightValidationError):
            await service.record_action(task, action_request, result, reduced)
        settled, planned = await service.commit_logical_change(task, reduced)

        self.assertEqual(settled.code_generation, state.code_generation + 1)
        self.assertIsNone(planned)

    async def test_low_risk_change_completes_with_planner_attestation(self) -> None:
        task = await self.task()
        write = ToolCall(
            "write", "write_file", {"path": "README.md", "content": "documentation\n"}
        )
        model = FakeModelClient(
            ((ModelEvent(ModelEventKind.TOOL_CALL, tool_call=write), _completed()), (_completed(),))
        )
        dispatcher = WorkspaceDispatcher(self.root)
        _ = [
            event async for event in AgentEngine(
                model, FakeContextBuilder(), dispatcher, self.sessions,
                verification=self.service(),
            ).run("document", thread_id=task.thread_id, task=task)
        ]

        evidence = await self.sessions.list_verification_evidence(task.id)
        self.assertEqual([item.name for item in dispatcher.requests], ["write_file"])
        self.assertTrue(any(item.provenance is EvidenceProvenance.SYSTEM_PLANNER for item in evidence))
        self.assertEqual((await self.sessions.load_task(task.id)).status, TaskStatus.COMPLETED)

    async def test_unplanned_targeted_test_cannot_satisfy_completion(self) -> None:
        task = await self.task()
        service = self.service()
        state = await service.prepare(task, TaskState.empty())
        edit_request = ActionRequest(
            "write", "write_file", {"path": "service.py", "content": "VALUE = 2\n"}
        )
        edit_result = ActionResult("write", "write_file", {"path": "service.py"})
        (self.root / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
        state = reduce_task_state(state, edit_request, edit_result)
        state = await service.record_action(task, edit_request, edit_result, state)
        verify_request = ActionRequest(
            "model-test", "run_verification",
            {"kind": "python_unittest", "targets": ["tests/test_service.py"]},
        )
        state = await service.record_action(
            task,
            verify_request,
            ActionResult("model-test", "run_verification", {"returncode": 0}),
            state,
        )

        assessment = await service.assess(task, state)
        self.assertNotEqual(assessment.assessment.kind, CompletionKind.VERIFIED)


if __name__ == "__main__":
    unittest.main()
