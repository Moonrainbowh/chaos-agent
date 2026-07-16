from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ActionRequest, ActionResult, Message, ToolCall  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.core.protocols import SessionRepository  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent.sessions.models import GoalStatus, ThreadStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.core.limits import EngineLimits  # noqa: E402
from code_agent.core.models import Usage  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.core.task import TaskStatus  # noqa: E402
from code_agent.core.completion_contract import AcceptanceCriterion, CriterionRequirement, CriterionStrength, TaskContractRevision, TaskIntent  # noqa: E402
from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord  # noqa: E402


class SQLiteSessionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_core_protocol_round_trips_messages_events_after_restart(self) -> None:
        repository: SessionRepository = self.repository
        thread_id = await repository.create_thread()
        message = Message(
            role="assistant",
            content="working",
            tool_calls=(
                ToolCall(id="call-1", name="read_file", arguments={"path": "a.py"}),
            ),
        )
        event = AgentEvent(
            EventKind.ACTION_COMPLETED,
            {"ok": True, "usage": {"input_tokens": 4}},
            datetime(2026, 7, 11, 1, 2, 3, tzinfo=timezone.utc),
        )

        await repository.append_message(thread_id, message)
        await repository.append_event(thread_id, event)

        reopened = SQLiteSessionRepository(self.database)
        self.assertEqual(tuple(await reopened.load_messages(thread_id)), (message,))
        self.assertEqual(await reopened.load_events(thread_id), (event,))

    async def test_tui_thread_list_orders_activity_and_exposes_preview(self) -> None:
        first = await self.repository.create_thread(title="First task")
        await asyncio.sleep(0.01)
        second = await self.repository.create_thread(title="Second task")
        await asyncio.sleep(0.01)
        await self.repository.append_message(first, Message("user", "latest\nrequest"))

        summaries = await self.repository.list_threads()

        self.assertEqual([item.id for item in summaries], [first, second])
        self.assertEqual(summaries[0].title, "First task")
        self.assertEqual(summaries[0].message_count, 1)
        self.assertEqual(summaries[0].last_message_preview, "latest request")
        self.assertEqual(summaries[0].status, ThreadStatus.ACTIVE)

    async def test_archived_threads_are_hidden_by_default(self) -> None:
        thread_id = await self.repository.create_thread(title="Done")

        await self.repository.archive_thread(thread_id)

        self.assertEqual(await self.repository.list_threads(), ())
        archived = await self.repository.list_threads(include_archived=True)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].status, ThreadStatus.ARCHIVED)

    async def test_accepted_partial_task_is_terminal_for_default_task_list(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        await self.repository.transition_task(task.id, TaskStatus.RUNNING)
        await self.repository.transition_task(task.id, TaskStatus.WAITING_DECISION)
        await self.repository.transition_task(task.id, TaskStatus.ACCEPTED_PARTIAL, "user accepted limitation")

        self.assertEqual(await self.repository.list_tasks(), ())
        self.assertEqual((await self.repository.list_tasks(include_terminal=True))[0].status, TaskStatus.ACCEPTED_PARTIAL)

    async def test_task_profile_facts_survive_persistence(self) -> None:
        thread_id = await self.repository.create_thread()
        contract = TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name), profile_id="company", model="gpt-test", protocol="responses", endpoint_host="api.example.test")
        task = await self.repository.create_task(thread_id, contract)

        restored = await SQLiteSessionRepository(self.database).load_task(task.id)

        self.assertEqual((restored.contract.profile_id, restored.contract.model, restored.contract.protocol, restored.contract.endpoint_host), ("company", "gpt-test", "responses", "api.example.test"))

    async def test_goals_and_checkpoints_persist_structured_metadata(self) -> None:
        thread_id = await self.repository.create_thread()
        goal_id = await self.repository.create_goal(
            thread_id, "Finish sessions", metadata={"priority": 1}
        )
        checkpoint_id = await self.repository.create_checkpoint(
            thread_id, "before migration", {"commit": "abc"}
        )

        await self.repository.update_goal(goal_id, GoalStatus.COMPLETED)

        reopened = SQLiteSessionRepository(self.database)
        goals = await reopened.list_goals(thread_id)
        checkpoints = await reopened.list_checkpoints(thread_id)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0].id, goal_id)
        self.assertEqual(goals[0].status, GoalStatus.COMPLETED)
        self.assertEqual(dict(goals[0].metadata), {"priority": 1})
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].id, checkpoint_id)
        self.assertEqual(dict(checkpoints[0].metadata), {"commit": "abc"})

    async def test_task_state_survives_reopen_and_reduces_atomically(self) -> None:
        repository: SessionRepository = self.repository
        thread_id = await repository.create_thread()
        state = TaskState(objective="repair startup", open_questions=("where?",))

        await repository.save_task_state(thread_id, state)
        self.assertEqual(await repository.load_task_state(thread_id), state)
        reduced = await repository.reduce_task_state(
            thread_id,
            ActionRequest("read-1", "read_file", {"path": "src/app.py"}),
            ActionResult("read-1", "read_file", {"text": "contents"}),
        )
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(reduced.files_read, ("src/app.py",))
        self.assertEqual(await reopened.load_task_state(thread_id), reduced)

    async def test_unknown_thread_fails_closed_for_all_owned_records(self) -> None:
        operations = (
            self.repository.load_messages("missing"),
            self.repository.load_events("missing"),
            self.repository.append_message("missing", Message("user", "hello")),
            self.repository.append_event(
                "missing", AgentEvent(EventKind.ERROR, {"message": "failed"})
            ),
            self.repository.create_goal("missing", "goal"),
            self.repository.create_checkpoint("missing", "checkpoint"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(SessionNotFound):
                    await operation

    async def test_task_budget_survives_restart_and_refuses_over_budget_reservation(self) -> None:
        thread_id = await self.repository.create_thread()
        limits = EngineLimits(max_agent_rounds=2, max_tool_calls=3)
        created = await self.repository.get_or_create_task_budget(thread_id, "model-a", limits)
        reserved = await self.repository.reserve_task_budget(thread_id, model_turns=1, tool_calls=2)
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(created.model_turns, 0)
        self.assertEqual(reserved.model_turns, 1)
        self.assertEqual(reserved.tool_calls, 2)
        self.assertIsNone(await reopened.reserve_task_budget(thread_id, tool_calls=2))

    async def test_task_controls_and_supervision_budget_survive_restart(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)),
        )
        await self.repository.get_or_create_task_budget(thread_id, "model-a", EngineLimits())
        await self.repository.consume_task_usage(task.id, Usage(4, 3))
        await self.repository.observe_task_validation(task.id, "pytest|1", 1)
        await self.repository.observe_task_validation(task.id, "pytest|1", 1)
        await self.repository.record_task_active_seconds(task.id, 42)
        await self.repository.record_task_control(task.id, "stop editing and inspect tests")

        reopened = SQLiteSessionRepository(self.database)
        budget = await reopened.load_task_budget(task.id)

        self.assertEqual((budget.input_tokens, budget.output_tokens), (4, 3))
        self.assertEqual((budget.repair_cycles, budget.repeated_failures), (2, 2))
        self.assertEqual(budget.active_seconds, 42)
        self.assertEqual(
            await reopened.consume_task_controls(task.id),
            ("stop editing and inspect tests",),
        )
        self.assertEqual(await reopened.consume_task_controls(task.id), ())

    async def test_token_limit_and_warning_thresholds_survive_restart(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        await self.repository.get_or_create_task_budget(thread_id, "model-a", EngineLimits(max_total_tokens=10))
        await self.repository.consume_task_usage(task.id, Usage(8, 0))

        self.assertEqual(await self.repository.mark_task_budget_warnings(task.id), (80,))
        self.assertEqual(await self.repository.mark_task_budget_warnings(task.id), ())
        await self.repository.consume_task_usage(task.id, Usage(1, 0))
        self.assertEqual(await self.repository.mark_task_budget_warnings(task.id), (90,))
        reopened = SQLiteSessionRepository(self.database)
        self.assertEqual((await reopened.load_task_budget(task.id)).limits.max_total_tokens, 10)

    async def test_reconciliation_interrupts_only_a_stale_execution_once(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        running = await self.repository.transition_task(task.id, TaskStatus.RUNNING)
        await self.repository.register_task_execution(running.id, "instance-a", 123, 45.0)

        self.assertEqual(await self.repository.reconcile_stale_tasks(lambda _pid, _created: False), (running.id,))
        self.assertEqual((await self.repository.load_task(running.id)).status, TaskStatus.INTERRUPTED)
        checkpoints = await self.repository.list_checkpoints(thread_id)
        self.assertEqual(len(checkpoints), 1)
        self.assertIsNone(checkpoints[0].message_sequence)
        self.assertIsNone(checkpoints[0].event_sequence)
        self.assertEqual(await self.repository.reconcile_stale_tasks(lambda _pid, _created: False), ())

    async def test_contract_revisions_and_evidence_ledger_survive_restart(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),))
        await self.repository.save_task_contract_revision(task.id, contract)
        await self.repository.begin_verification_run("run-1", task.id, 1, "subject")
        evidence = EvidenceRecord.from_output("evidence-1", "tests", EvidenceOutcome.PASS, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "ok", "passed")
        await self.repository.append_verification_evidence("run-1", task.id, evidence)
        await self.repository.close_verification_run("run-1", "completed")
        reopened = SQLiteSessionRepository(self.database)
        self.assertEqual((await reopened.load_task_contract_revision(task.id)).revision, 1)
        self.assertEqual(await reopened.list_verification_evidence(task.id), (evidence,))

    async def test_finalize_task_requires_current_completed_required_evidence(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),))
        await self.repository.save_task_contract_revision(task.id, contract)
        await self.repository.transition_task(task.id, TaskStatus.RUNNING)
        await self.repository.transition_task(task.id, TaskStatus.VERIFYING)
        await self.repository.begin_verification_run("run-final", task.id, 1, "subject")
        evidence = EvidenceRecord.from_output("evidence-final", "tests", EvidenceOutcome.PASS, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "ok", "passed")
        await self.repository.append_verification_evidence("run-final", task.id, evidence)
        await self.repository.close_verification_run("run-final", "completed")

        completed = await self.repository.finalize_task(task.id, "run-final", contract, 1, "subject")

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        with self.assertRaises(ValueError):
            await self.repository.finalize_task(task.id, "run-final", contract, 1, "subject")

    async def test_recovery_marks_open_verification_runs_interrupted(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        await self.repository.begin_verification_run("run-open", task.id, 0, "subject")

        self.assertEqual(await self.repository.interrupt_open_verification_runs(task.id), 1)
        self.assertEqual(await self.repository.interrupt_open_verification_runs(task.id), 0)

    async def test_finalize_task_accepts_current_required_evidence_from_completed_runs(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(thread_id, TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)))
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (
            AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),
            AcceptanceCriterion("build", "build passes", CriterionRequirement.REQUIRED, CriterionStrength.INTEGRITY),
        ))
        await self.repository.save_task_contract_revision(task.id, contract)
        await self.repository.transition_task(task.id, TaskStatus.RUNNING)
        await self.repository.transition_task(task.id, TaskStatus.VERIFYING)
        for run_id, criterion in (("run-tests", "tests"), ("run-build", "build")):
            await self.repository.begin_verification_run(run_id, task.id, 1, "subject")
            evidence = EvidenceRecord.from_output(run_id, criterion, EvidenceOutcome.PASS, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "ok", "passed")
            await self.repository.append_verification_evidence(run_id, task.id, evidence)
            await self.repository.close_verification_run(run_id, "completed")

        completed = await self.repository.finalize_task(task.id, "run-build", contract, 1, "subject")

        self.assertEqual(completed.status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
