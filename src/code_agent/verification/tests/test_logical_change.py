from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.completion_contract import CompletionKind
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.task_state import TaskState
from code_agent.core.verification_state import VerifierOutcome
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.verification.planner import VerificationPhase
from code_agent.verification.task_service import LedgerTaskVerificationService


class LogicalChangeTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        container = Path(self.temporary.name).resolve()
        self.root = container / "workspace"
        self.root.mkdir()
        (self.root / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
        (self.root / "service.py").write_text("import models\n", encoding="utf-8")
        (self.root / "test_service.py").write_text("import service\n", encoding="utf-8")
        self.sessions = SQLiteSessionRepository(container / "state.sqlite3")
        thread_id = await self.sessions.create_thread()
        self.task = await self.sessions.create_task(
            thread_id,
            TaskContract(
                "refactor user service",
                TaskAuthorization.local_workspace(str(self.root)),
            ),
        )
        self.service = LedgerTaskVerificationService(self.root, self.sessions)
        self.state = await self.service.prepare(self.task, TaskState.empty())

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_logical_change_batches_multiple_edits_into_single_generation(self) -> None:
        initial_generation = self.state.code_generation
        initial_hash = self.state.subject_hash

        # 1. Begin logical change transaction
        self.service.begin_logical_change(self.task.id)
        self.assertTrue(self.service.in_logical_change(self.task.id))

        # 2. Edit 1: models.py
        (self.root / "models.py").write_text("class User:\n    name: str\n", encoding="utf-8")
        state1 = await self.service.record_action(
            self.task,
            ActionRequest("edit-1", "write_file", {"path": "models.py", "content": "..."}),
            ActionResult("edit-1", "write_file", {"path": "models.py"}),
            self.state,
        )
        # Generation must NOT advance during intra-turn edits
        self.assertEqual(state1.code_generation, initial_generation)
        self.assertEqual(self.service.get_pending_changes(self.task.id), ("models.py",))

        # 3. Edit 2: service.py
        (self.root / "service.py").write_text("import models\ndef get_user(): pass\n", encoding="utf-8")
        state2 = await self.service.record_action(
            self.task,
            ActionRequest("edit-2", "write_file", {"path": "service.py", "content": "..."}),
            ActionResult("edit-2", "write_file", {"path": "service.py"}),
            state1,
        )
        self.assertEqual(state2.code_generation, initial_generation)
        self.assertEqual(self.service.get_pending_changes(self.task.id), ("models.py", "service.py"))

        # 4. Edit 3: test_service.py
        (self.root / "test_service.py").write_text("import service\ndef test_user(): pass\n", encoding="utf-8")
        state3 = await self.service.record_action(
            self.task,
            ActionRequest("edit-3", "write_file", {"path": "test_service.py", "content": "..."}),
            ActionResult("edit-3", "write_file", {"path": "test_service.py"}),
            state2,
        )
        self.assertEqual(state3.code_generation, initial_generation)

        # 5. Commit at Turn Settle boundary
        settled_state, plan = await self.service.commit_logical_change(
            self.task, state3, phase=VerificationPhase.LOCAL_MILESTONE
        )

        # Generation advances by exactly 1 for the entire batch
        self.assertEqual(settled_state.code_generation, initial_generation + 1)
        self.assertNotEqual(settled_state.subject_hash, initial_hash)
        self.assertFalse(self.service.in_logical_change(self.task.id))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.changed_files, ("models.py", "service.py", "test_service.py"))
        self.assertEqual(plan.phase, VerificationPhase.LOCAL_MILESTONE)

    async def test_logical_change_rollback_discards_pending(self) -> None:
        initial_generation = self.state.code_generation
        self.service.begin_logical_change(self.task.id)

        await self.service.record_action(
            self.task,
            ActionRequest("edit-1", "write_file", {"path": "models.py", "content": "..."}),
            ActionResult("edit-1", "write_file", {"path": "models.py"}),
            self.state,
        )
        self.assertEqual(len(self.service.get_pending_changes(self.task.id)), 1)

        # Rollback
        self.service.rollback_logical_change(self.task.id)
        self.assertFalse(self.service.in_logical_change(self.task.id))

        # Subsequent commit produces no changes
        settled_state, plan = await self.service.commit_logical_change(self.task, self.state)
        self.assertEqual(settled_state.code_generation, initial_generation)
        self.assertIsNone(plan)

    async def test_command_execution_auto_commits_pending_edits(self) -> None:
        initial_generation = self.state.code_generation
        self.service.begin_logical_change(self.task.id)

        await self.service.record_action(
            self.task,
            ActionRequest("edit-1", "write_file", {"path": "models.py", "content": "..."}),
            ActionResult("edit-1", "write_file", {"path": "models.py"}),
            self.state,
        )

        # Attempted command triggers auto-commit of pending edits
        advanced = await self.service.record_action(
            self.task,
            ActionRequest("run-cmd", "run_command", {"command": "python test.py"}),
            ActionResult("run-cmd", "run_command", {"stdout": ""}, metadata={"execution_attempted": True}),
            self.state,
        )
        self.assertEqual(advanced.code_generation, initial_generation + 1)
        self.assertFalse(self.service.in_logical_change(self.task.id))

    async def test_verification_request_auto_commits_pending_edits(self) -> None:
        initial_generation = self.state.code_generation
        self.service.begin_logical_change(self.task.id)

        await self.service.record_action(
            self.task,
            ActionRequest("edit-1", "write_file", {"path": "models.py", "content": "..."}),
            ActionResult("edit-1", "write_file", {"path": "models.py"}),
            self.state,
        )

        # Verification requested: auto-commits pending edits so evidence matches committed generation
        verified = await self.service.record_action(
            self.task,
            ActionRequest("verify", "run_verification", {"kind": "python_unittest"}),
            ActionResult("verify", "run_verification", {"kind": "python_unittest", "returncode": 0}),
            self.state,
        )
        self.assertEqual(verified.code_generation, initial_generation + 1)
        assessment = await self.service.assess(self.task, verified)
        self.assertEqual(assessment.outcome, VerifierOutcome.PASS)
        self.assertEqual(assessment.generation, initial_generation + 1)

    async def test_standalone_edit_advances_generation_immediately(self) -> None:
        # Without begin_logical_change, legacy single-call behavior is preserved
        initial_generation = self.state.code_generation
        advanced = await self.service.record_action(
            self.task,
            ActionRequest("single-edit", "write_file", {"path": "models.py", "content": "..."}),
            ActionResult("single-edit", "write_file", {"path": "models.py"}),
            self.state,
        )
        self.assertEqual(advanced.code_generation, initial_generation + 1)


if __name__ == "__main__":
    unittest.main()
