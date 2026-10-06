"""Production ledger completion must prove the current risk-selected final gate."""
import tempfile
import unittest
from pathlib import Path

from code_agent.core.completion_contract import CompletionKind
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.verification.planner import VerificationPhase
from code_agent.verification.task_service import LedgerTaskVerificationService


class FinalPlanCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1'\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_service.py").write_text("import service\n")
        self.sessions = SQLiteSessionRepository(Path(self.temporary.name) / "sessions.sqlite3")
        thread = await self.sessions.create_thread()
        self.task = await self.sessions.create_task(thread, TaskContract(
            "Modify feature", TaskAuthorization.local_workspace(str(self.root))))
        self.service = LedgerTaskVerificationService(self.root, self.sessions)

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def state(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path != "pyproject.toml":
            target.write_text("VALUE = 2\n")
        return await self.service.prepare(self.task, TaskState(files_changed=(path,)))

    async def record(self, state, call, *, error=None):
        request = ActionRequest(call.id, call.name, call.arguments)
        output = {"returncode": 0, "stdout": "completed", "kind": call.arguments["kind"]}
        if error:
            output = {"error": "verification unavailable", "detail": error}
        return await self.service.record_action(self.task, request,
            ActionResult(call.id, call.name, output, is_error=bool(error)), state)

    async def test_medium_selects_related_tests_and_rejects_unrelated_proof(self):
        state = await self.state("service.py")
        from code_agent.core.models import ToolCall
        state = await self.record(state, ToolCall("model-selected", "run_verification",
            {"kind": "python_unittest", "targets": ["unrelated.py"]}))
        self.assertNotEqual((await self.service.assess(self.task, state)).assessment.kind,
                            CompletionKind.VERIFIED)
        planned = await self.service.suggest_verification(self.task, state)
        self.assertEqual(tuple(planned.arguments["targets"]), ("tests/test_service.py",))
        state = await self.record(state, planned)
        self.assertEqual((await self.service.assess(self.task, state)).assessment.kind,
                         CompletionKind.VERIFIED)

    async def test_high_requires_full_tests_not_passing_targeted_milestone(self):
        state = await self.state("src/core/service.py")
        plan = self.service.planner.plan(state.files_changed, VerificationPhase.LOCAL_MILESTONE)
        milestone = self.service.milestone_verification(self.task, plan)
        state = await self.record(state, milestone)
        self.assertNotEqual((await self.service.assess(self.task, state)).assessment.kind,
                            CompletionKind.VERIFIED)
        final = await self.service.suggest_verification(self.task, state)
        self.assertNotIn("targets", final.arguments)
        state = await self.record(state, final)
        self.assertEqual((await self.service.assess(self.task, state)).assessment.kind,
                         CompletionKind.VERIFIED)

    async def test_critical_risk_requires_tests_and_build_with_unchanged_contract(self):
        state = await self.state("pyproject.toml")
        before = await self.sessions.load_task_contract_revision(self.task.id)
        tests = await self.service.suggest_verification(self.task, state)
        self.assertEqual(tests.arguments["kind"], "python_unittest")
        state = await self.record(state, tests)
        self.assertNotEqual((await self.service.assess(self.task, state)).assessment.kind,
                            CompletionKind.VERIFIED)
        build = await self.service.suggest_verification(self.task, state)
        self.assertEqual(build.arguments["kind"], "python_build")
        state = await self.record(state, build)
        self.assertEqual((await self.service.assess(self.task, state)).assessment.kind,
                         CompletionKind.VERIFIED)
        self.assertEqual(await self.sessions.load_task_contract_revision(self.task.id), before)

    async def test_unavailable_preserves_specific_diagnostic_without_claiming_pass(self):
        state = await self.state("service.py")
        planned = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, planned, error="Local test runtime is unavailable")
        assessment = await self.service.assess(self.task, state)
        self.assertEqual(assessment.assessment.kind, CompletionKind.UNVERIFIED)
        self.assertIn("Local test runtime is unavailable", assessment.diagnostics)

    async def test_critical_later_failed_tests_invalidate_earlier_tests_pass(self):
        state = await self.state("pyproject.toml")
        tests = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, tests)
        build = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, build)
        # Model replay cannot forge a Host final-tests criterion; use a fresh
        # Host registry entry representing a retry of that exact final gate.
        plan = self.service.planner.plan(state.files_changed, VerificationPhase.FINAL_GATE)
        retry = self.service._planned_calls.create(self.task.id, plan,
            "python_unittest", ".", (), "tests")
        state = await self.record(state, retry, error="Local test runtime is unavailable")
        self.assertNotEqual((await self.service.assess(self.task, state)).assessment.kind,
                            CompletionKind.VERIFIED)

    async def test_successful_arbitrary_command_is_not_requirement_proof(self):
        state = await self.state("service.py")
        state = await self.service.record_action(self.task,
            ActionRequest("shell", "run_command", {"command": "exit 0"}),
            ActionResult("shell", "run_command", {"returncode": 0},
                         metadata={"execution_attempted": True}), state)
        self.assertEqual((await self.service.assess(self.task, state)).assessment.kind,
                         CompletionKind.UNVERIFIED)

    async def test_progress_pass_is_deduplicated_and_expires_with_subject(self):
        from dataclasses import replace
        state = await self.state("service.py")
        self.assertEqual(await self.service.progress_fingerprint(self.task, state), "")
        planned = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, planned, error="Unavailable")
        self.assertEqual(await self.service.progress_fingerprint(self.task, state), "")
        planned = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, planned)
        first = await self.service.progress_fingerprint(self.task, state)
        self.assertTrue(first)
        repeated = self.service._planned_calls.create(self.task.id,
            self.service.planner.plan(state.files_changed, VerificationPhase.FINAL_GATE),
            "python_unittest", ".", ("tests/test_service.py",), "tests")
        state = await self.record(state, repeated)
        self.assertEqual(await self.service.progress_fingerprint(self.task, state), first)
        self.assertEqual(await self.service.progress_fingerprint(self.task,
            replace(state, code_generation=state.code_generation + 1)), "")

    async def test_failed_retry_never_retracts_success_progress_but_invalidates_completion(self):
        state = await self.state("service.py")
        planned = await self.service.suggest_verification(self.task, state)
        state = await self.record(state, planned)
        passed = await self.service.progress_fingerprint(self.task, state)
        self.assertTrue(passed)
        self.assertEqual((await self.service.assess(self.task, state)).assessment.kind,
                         CompletionKind.VERIFIED)
        for outcome in ("failed", "unavailable"):
            plan = self.service.planner.plan(state.files_changed, VerificationPhase.FINAL_GATE)
            retry = self.service._planned_calls.create(self.task.id, plan,
                "python_unittest", ".", ("tests/test_service.py",), "tests")
            request = ActionRequest(retry.id, retry.name, retry.arguments)
            output = ({"returncode": 1, "stderr": "test failed"} if outcome == "failed"
                      else {"error": "verification unavailable", "detail": "runtime missing"})
            state = await self.service.record_action(self.task, request,
                ActionResult(retry.id, retry.name, output, is_error=True), state)
            self.assertEqual(await self.service.progress_fingerprint(self.task, state), passed)
            self.assertNotEqual((await self.service.assess(self.task, state)).assessment.kind,
                                CompletionKind.VERIFIED)
            self.assertIsNotNone(await self.service.suggest_verification(self.task, state))
