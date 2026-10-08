import json
import unittest

from code_agent.core.host_progress import observe_host_progress, interaction_revision
from code_agent.core.limits import TaskProgressSnapshot
from code_agent.core.models import ActionRequest, ActionResult, Message, ToolCall


def pair(index, name, arguments, output, *, error=False, metadata=None):
    call = ToolCall(str(index), name, arguments)
    result = ActionResult(call.id, name, output, error, metadata or {})
    return (Message(role="assistant", content="继续查看", tool_calls=(call,)),
        Message(role="tool", name=name, tool_call_id=call.id, content=json.dumps(result.to_dict())))


class HostProgressTests(unittest.TestCase):
    def facts(self, messages, objective="Inspect src/auth/", limit=8, dispatcher=None):
        return observe_host_progress(tuple(messages), objective, dispatcher,
            candidate_limit=limit, hard_tool_limit=50)

    def test_related_path_prefix_is_segment_based(self):
        a = self.facts(pair(1, "read_file", {"path": "src/auth/a.py"}, {"text": "a"}))
        b = self.facts(pair(1, "read_file", {"path": "src/auth-other/a.py"}, {"text": "a"}))
        self.assertEqual((a.related_count, b.related_count), (1, 0))
        self.assertEqual((a.candidate_count, b.candidate_count), (1, 1))

    def test_opaque_errors_and_context_changes_have_no_progress(self):
        messages = pair(1, "opaque", {}, {"text": "new"}) + pair(2, "read_file",
            {"path": "src/auth/a.py"}, {"text": "new"}, error=True) + pair(3, "new_context", {}, {"ok": True})
        self.assertEqual(self.facts(messages).candidate_count, 0)

    def test_read_changed_ids_timing_and_input_metadata_do_not_advance(self):
        first = pair(1, "read_file", {"path": "src/auth/a.py"}, {"text": "same", "duration_ms": 1})
        second = pair(2, "read_file", {"path": "src/auth/a.py", "start_line": 5},
            {"text": "same", "duration_ms": 99})
        self.assertEqual(self.facts(first).renewal_digest, self.facts(first + second).renewal_digest)
        self.assertEqual(self.facts(first + second).candidate_count, 1)

    def test_cumulative_reads_do_not_advance_on_ab_rotation(self):
        a = pair(1, "read_file", {"path": "src/auth/a.py"}, {"text": "a"})
        b = pair(2, "read_file", {"path": "src/auth/b.py"}, {"text": "b"})
        self.assertEqual(self.facts(a + b).renewal_digest,
            self.facts(a + b + pair(3, "read_file", {"path": "src/auth/a.py"}, {"text": "a"})).renewal_digest)

    def test_candidate_saturation_does_not_reaccept_evicted_facts(self):
        a = pair(1, "read_file", {"path": "elsewhere/a.py"}, {"text": "a"})
        b = pair(2, "read_file", {"path": "elsewhere/b.py"}, {"text": "b"})
        c = pair(3, "read_file", {"path": "elsewhere/c.py"}, {"text": "c"})
        self.assertEqual(self.facts(a + b, limit=2).candidate_digest,
                         self.facts(a + b + c + a, limit=2).candidate_digest)
        self.assertEqual(self.facts(a + b + c).related_count, 0)

    def test_explicit_symbol_search_creates_actual_path_chain(self):
        search = pair(1, "search_text", {"pattern": "startup_delay"},
            {"matches": [{"path": "app/main.py", "text": "startup_delay"}]})
        read = pair(2, "read_file", {"path": "app/main.py"}, {"text": "actual code"})
        self.assertEqual(self.facts(search + read, "Explain startup_delay").related_count, 2)
        self.assertEqual(self.facts(search + read, "Explain performance").related_count, 0)

    def test_search_empty_or_model_chosen_query_does_not_seed_chain(self):
        search = pair(1, "search_text", {"pattern": "unrelated"},
            {"matches": [{"path": "app/main.py"}]})
        read = pair(2, "read_file", {"path": "app/main.py"}, {"text": "code"})
        self.assertEqual(self.facts(search + read, "Explain startup_delay").related_count, 0)

    def test_failure_is_not_renewal_but_same_related_signature_success_is(self):
        failure = pair(1, "read_file", {"path": "src/auth/a.py"}, {"error": "busy"}, error=True)
        initial = self.facts(())
        failed = self.facts(failure)
        self.assertEqual(initial.renewal_digest, failed.renewal_digest)
        success = pair(2, "read_file", {"path": "src/auth/a.py"}, {"text": "ready"})
        self.assertEqual(self.facts(failure + success).resolved_count, 1)

    def test_failure_fingerprint_is_diagnostic_not_positive_digest(self):
        self.assertEqual(TaskProgressSnapshot().digest,
            TaskProgressSnapshot(failure_fingerprint="changed failure").digest)

    def test_host_alias_resolution_uses_original_pairs(self):
        class Host:
            def resolve_supervision_action(self, request):
                return ActionRequest(request.id, "read_file", {"path": request.arguments["path"]})
        messages = pair(1, "read", {"operation": "file", "path": "src/auth/a.py"}, {"text": "code"})
        self.assertEqual(self.facts(messages, dispatcher=Host()).related_count, 1)

    def test_unpaired_or_wrong_id_result_is_not_progress(self):
        messages = pair(1, "read_file", {"path": "src/auth/a.py"}, {"text": "new"})
        self.assertEqual(self.facts(messages[1:]).related_count, 0)

    def test_explicit_whole_repository_allows_actual_reads(self):
        messages = pair(1, "read_file", {"path": "any/actual.py"}, {"text": "code"})
        self.assertEqual(self.facts(messages, "Do a repository-wide investigation").related_count, 1)

    def test_negated_broad_scope_is_not_a_positive_progress_seed(self):
        messages = pair(1, "read_file", {"path": "src/unit_0.py"}, {"text": "code"})
        for objective in (
            "Do not inspect the whole repository; explain src/auth/ only.",
            "Don't inspect the entire repository; explain src/auth/ only.",
            "Avoid repository-wide investigation; explain src/auth/ only.",
            "不要检查全仓库；只解释 src/auth/。",
            "无需扫描整个仓库，只解释 src/auth/。",
            "不必调查全项目；只解释 src/auth/。",
            "请勿调查全仓库；只解释 src/auth/。",
            "不得扫描整个项目；只解释 src/auth/。",
            "不允许检查整个仓库；只解释 src/auth/。",
            "别检查全项目；只解释 src/auth/。",
        ):
            with self.subTest(objective=objective):
                facts = self.facts(messages, objective)
                self.assertEqual(facts.related_count, 0)
                self.assertEqual(facts.candidate_count, 1)

    def test_affirmative_scope_survives_a_separate_unrelated_negative_clause(self):
        messages = pair(1, "read_file", {"path": "src/unit_0.py"}, {"text": "code"})
        for objective in (
            "Inspect the whole repository; do not edit any files.",
            "Do not edit files; inspect the entire repository.",
            "检查全仓库；不要修改文件。",
        ):
            with self.subTest(objective=objective):
                self.assertEqual(self.facts(messages, objective).related_count, 1)

    def test_explicitly_excluded_read_paths_are_not_scope_seeds(self):
        unrelated = pair(1, "read_file", {"path": "src/unrelated/a.py"}, {"text": "code"})
        related = pair(2, "read_file", {"path": "src/auth/a.py"}, {"text": "code"})
        for objective in (
            "Explain src/auth/ only; do not inspect src/unrelated/.",
            "只解释 src/auth/；请勿查看 src/unrelated/。",
        ):
            with self.subTest(objective=objective):
                self.assertEqual(self.facts(unrelated, objective).related_count, 0)
                self.assertEqual(self.facts(related, objective).related_count, 1)
        self.assertEqual(self.facts(related, "Explain src/auth/; do not edit src/auth/.").related_count, 1)

    def test_parent_and_broad_paths_do_not_override_explicit_read_exclusion(self):
        messages = pair(1, "read_file", {"path": "src/unrelated/a.py"}, {"text": "code"})
        for objective in (
            "Explain src/; do not inspect src/unrelated/.",
            "Inspect the whole repository; do not inspect src/unrelated/.",
        ):
            self.assertEqual(self.facts(messages, objective).related_count, 0)

    def test_search_and_slices_cannot_reintroduce_excluded_paths(self):
        objective = "Explain src/ and `startup_delay`; do not inspect src/unrelated/."
        search = pair(1, "search_text", {"pattern": "startup_delay"},
            {"matches": [{"path": "src/unrelated/a.py", "text": "startup_delay"}]})
        read = pair(2, "read_file", {"path": "src/unrelated/a.py"}, {"text": "code"})
        slices = pair(3, "read_code_slices", {},
            {"slices": [{"path": "src/unrelated/a.py", "text": "code"}]})
        self.assertEqual(self.facts(search + read + slices, objective).related_count, 0)

    def test_sentence_clause_boundary_preserves_real_file_extension(self):
        messages = pair(1, "read_file", {"path": "src/auth/a.py"}, {"text": "code"})
        self.assertEqual(self.facts(messages, "Explain src/auth/a.py. Do not edit files.").related_count, 1)

    def test_initial_depth_tier_uses_only_existing_affirmative_depth_terms(self):
        from code_agent.core.completion_contract import TaskIntent
        from code_agent.core.limits import BudgetLeaseTier, select_budget_lease
        from code_agent.core.task import TaskAuthorization, TaskContract
        def tier(objective):
            return select_budget_lease(TaskContract(objective,
                TaskAuthorization.local_workspace("C:/repo"), intent=TaskIntent.ANALYZE))
        for objective in (
            "不要调查全仓库；只解释 src/auth/。",
            "请勿进行深度调查；只解释 src/auth/。",
            "Do not do a deep investigation; explain src/auth/ only.",
            "Avoid repository-wide review; explain src/auth/ only.",
        ):
            with self.subTest(objective=objective):
                self.assertIs(tier(objective), BudgetLeaseTier.QUICK)
        for objective in (
            "深度调查全仓库；不要修改文件。",
            "Do a deep investigation; do not edit files.",
            "Do not edit files; perform a repository-wide review.",
        ):
            with self.subTest(objective=objective):
                self.assertIs(tier(objective), BudgetLeaseTier.DEEP)

    def test_explicitly_denied_search_symbol_does_not_seed_paths(self):
        messages = pair(1, "search_text", {"pattern": "secret_api"},
            {"matches": [{"path": "src/elsewhere.py", "text": "secret_api"}]})
        self.assertEqual(self.facts(messages,
            "Explain src/auth/; do not search secret_api.").related_count, 0)

    def test_history_reads_are_bounded_candidates_without_renewal(self):
        messages = pair(1, "notes_read_file", {"path": "checkpoint.md"},
            {"path": "checkpoint.md", "revision": 1, "text": "original evidence"})
        repeated = pair(2, "notes_read_file", {"path": "checkpoint.md"},
            {"path": "checkpoint.md", "revision": 2, "text": "original evidence"})
        facts = self.facts(messages + repeated)
        self.assertEqual((facts.candidate_count, facts.related_count), (1, 0))
        self.assertEqual(facts.renewal_digest, self.facts(()).renewal_digest)

    def test_contract_disclosure_needs_valid_host_result_and_only_counts_once(self):
        from code_agent.capabilities.catalog import contract_result
        from code_agent.core.models import ToolDefinition
        tools = (ToolDefinition("notes_read_file", "Read a note", {"type": "object"}),)
        messages = []
        for i in range(2):
            call = ToolCall(str(i), "load_tool_contract", {"name": "notes_read_file"})
            result = contract_result(ActionRequest(call.id, call.name, call.arguments), tools)
            messages.extend((Message(role="assistant", tool_calls=(call,)),
                Message(role="tool", name=call.name, tool_call_id=call.id, content=json.dumps(result.to_dict()))))
        self.assertEqual((self.facts(messages).candidate_count, self.facts(messages).related_count), (1, 0))
        self.assertEqual(self.facts(pair(4, "load_tool_contract", {}, {"name": "fake", "digest": "a" * 64})).candidate_count, 0)

    def test_actual_same_command_retry_resolves_failure_but_exit_zero_alone_does_not(self):
        arguments = {"command": "python validate.py"}
        success = pair(2, "run_command", arguments, {"returncode": 0}, metadata={"execution_attempted": True})
        self.assertEqual(self.facts(success).resolved_count, 0)
        denied = pair(1, "run_command", arguments, {"error": "policy denied"}, error=True)
        self.assertEqual(self.facts(denied + success).resolved_count, 0)
        failed = pair(1, "run_command", arguments, {"returncode": 1}, error=True,
                      metadata={"execution_attempted": True})
        self.assertEqual(self.facts(failed + success).resolved_count, 1)

    def test_duplicate_resume_input_does_not_become_steering_progress(self):
        first = Message(role="user", content="inspect src/")
        self.assertEqual(interaction_revision((first, first)), 1)
        self.assertEqual(interaction_revision((first, Message(role="user", content="also inspect tests/"))), 2)

    def test_explicit_search_query_seeds_result_path(self):
        messages = pair(1, "search_text", {"pattern": "startup"},
            {"matches": [{"path": "app.py", "text": "startup"}]})
        self.assertEqual(self.facts(messages, "Search for startup and explain it").related_count, 1)


class LedgerNegativeProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_pass_revocation_does_not_renew_real_sqlite_lease_or_clear_guard(self):
        from code_agent.core.engine import AgentEngine
        from code_agent.core.exploration_repeat import ToolOnlyConvergenceGuard
        from code_agent.core.limits import EngineLimits, select_budget_lease, BudgetReserveStatus
        from code_agent.core.completion_contract import CompletionKind
        from code_agent.verification.planner import VerificationPhase
        from code_agent.verification.tests.test_final_plan_completion import FinalPlanCompletionTests

        # Reuse real ledger/SQLite setup. Verifier outcomes are controlled receipts;
        # this tests Core admission and convergence, not external tests actually running.
        fixture = FinalPlanCompletionTests()
        await fixture.asyncSetUp()
        try:
            state = await fixture.state("service.py")
            planned = await fixture.service.suggest_verification(fixture.task, state)
            state = await fixture.record(state, planned)
            await fixture.sessions.save_task_state(fixture.task.thread_id, state)
            engine = AgentEngine(object(), object(), object(), fixture.sessions,
                                 verification=fixture.service)
            budget = await fixture.sessions.get_or_create_task_budget(fixture.task.thread_id,
                "probe", EngineLimits(), select_budget_lease(fixture.task.contract))
            passed = await engine._task_progress_snapshot(fixture.task.thread_id, budget)
            admitted = await fixture.sessions.reserve_task_budget(fixture.task.thread_id,
                model_turns=budget.lease_model_turn_limit, progress=passed)
            self.assertTrue(admitted.accepted)
            guard = ToolOnlyConvergenceGuard()
            read = ToolCall("stall", "read_file", {"path": "service.py"})
            guard.observe(has_text=True, calls=[read])
            for outcome in ("failed", "unavailable"):
                plan = fixture.service.planner.plan(state.files_changed, VerificationPhase.FINAL_GATE)
                retry = fixture.service._planned_calls.create(fixture.task.id, plan,
                    "python_unittest", ".", ("tests/test_service.py",), "tests")
                output = ({"returncode": 1, "stderr": "failed"} if outcome == "failed"
                          else {"error": "verification unavailable", "detail": "runtime missing"})
                state = await fixture.service.record_action(fixture.task,
                    ActionRequest(retry.id, retry.name, retry.arguments),
                    ActionResult(retry.id, retry.name, output, is_error=True), state)
                await fixture.sessions.save_task_state(fixture.task.thread_id, state)
                after = await engine._task_progress_snapshot(fixture.task.thread_id, admitted.budget)
                self.assertEqual(after.digest, passed.digest)
                denied = await fixture.sessions.reserve_task_budget(fixture.task.thread_id,
                    model_turns=1, progress=after)
                self.assertIs(denied.status, BudgetReserveStatus.LEASE_EXHAUSTED)
                self.assertEqual(denied.budget.lease_renewals, 0)
                self.assertNotEqual((await fixture.service.assess(fixture.task, state)).assessment.kind,
                                    CompletionKind.VERIFIED)
                guard.observe(has_text=True, calls=[read], has_host_progress=after.digest != passed.digest)
            self.assertEqual(guard.exploration_count, 3)
        finally:
            await fixture.asyncTearDown()
