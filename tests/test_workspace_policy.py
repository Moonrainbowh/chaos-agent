from __future__ import annotations

import unittest
from unittest.mock import Mock

from code_agent_win.cli_options import (
    _split_isolation_option,
    _split_reclaim_option,
)
from code_agent_win.workspace_policy import (
    DEFAULT_MODE,
    ENV_VARIABLE,
    EXPLICIT_ISOLATION,
    NO_ISOLATION,
    WorkspaceIsolationRequest,
    WorkspaceMode,
    adaptive_isolation,
    isolated_tasks,
    plan_workspace,
    repository_probe_required,
    request_task_isolation,
    resolve_workspace_plan,
    task_isolation_request,
    workspace_mode,
)


class WorkspaceModeParsingTests(unittest.TestCase):
    def test_missing_variable_defaults_to_auto(self) -> None:
        self.assertIs(workspace_mode({}), DEFAULT_MODE)
        self.assertIs(DEFAULT_MODE, WorkspaceMode.AUTO)

    def test_supported_values_are_case_and_whitespace_insensitive(self) -> None:
        for raw, expected in (
            ("managed", WorkspaceMode.MANAGED),
            ("DIRECT", WorkspaceMode.DIRECT),
            ("  Auto  ", WorkspaceMode.AUTO),
        ):
            with self.subTest(raw=raw):
                self.assertIs(workspace_mode({ENV_VARIABLE: raw}), expected)

    def test_unsupported_value_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, ENV_VARIABLE):
            workspace_mode({ENV_VARIABLE: "worktree"})

    def test_blank_value_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, ENV_VARIABLE):
            workspace_mode({ENV_VARIABLE: "   "})


class IsolationRequestTests(unittest.TestCase):
    def test_no_reason_is_not_a_request(self) -> None:
        self.assertFalse(NO_ISOLATION.requested)
        self.assertEqual(NO_ISOLATION.reason(), "")

    def test_each_reason_requests_isolation(self) -> None:
        for request, reason in (
            (WorkspaceIsolationRequest(parallel=True), "parallel"),
            (WorkspaceIsolationRequest(background=True), "background"),
            (WorkspaceIsolationRequest(explicit=True), "explicit"),
        ):
            with self.subTest(reason=reason):
                self.assertTrue(request.requested)
                self.assertEqual(request.reason(), reason)


class AdaptiveIsolationTests(unittest.TestCase):
    def test_no_observed_fact_requests_no_isolation(self) -> None:
        request = adaptive_isolation()

        self.assertFalse(request.requested)
        self.assertEqual(request.reason(), "")

    def test_a_concurrent_writer_makes_the_task_parallel(self) -> None:
        request = adaptive_isolation(concurrent_writer=True)

        self.assertTrue(request.parallel)
        self.assertEqual(request.reason(), "parallel")

    def test_a_delegated_task_is_a_background_reason(self) -> None:
        request = adaptive_isolation(delegated=True)

        self.assertTrue(request.background)
        self.assertEqual(request.reason(), "background")

    def test_auto_isolates_a_concurrent_writer_in_a_repository(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.AUTO,
            is_git_repository=True,
            isolation=adaptive_isolation(concurrent_writer=True),
        )

        self.assertTrue(plan.isolated)
        self.assertEqual(plan.reason, "auto-isolation:parallel")

    def test_direct_mode_never_isolates_a_concurrent_writer(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.DIRECT,
            is_git_repository=True,
            isolation=adaptive_isolation(concurrent_writer=True),
        )

        self.assertFalse(plan.isolated)


class RepositoryProbeTests(unittest.TestCase):
    def test_direct_and_plain_auto_need_no_git_probe(self) -> None:
        self.assertFalse(repository_probe_required(WorkspaceMode.DIRECT))
        self.assertFalse(repository_probe_required(WorkspaceMode.AUTO))
        self.assertTrue(repository_probe_required(WorkspaceMode.MANAGED))
        self.assertTrue(
            repository_probe_required(
                WorkspaceMode.AUTO, WorkspaceIsolationRequest(parallel=True)
            )
        )


class WorkspacePlanTests(unittest.TestCase):
    def test_direct_mode_never_isolates_even_in_a_repository(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.DIRECT, is_git_repository=True
        )

        self.assertFalse(plan.isolated)
        self.assertEqual(plan.reason, "direct-mode")

    def test_auto_prefers_the_local_workspace_in_a_repository(self) -> None:
        plan = resolve_workspace_plan(WorkspaceMode.AUTO, is_git_repository=True)

        self.assertFalse(plan.isolated)
        self.assertEqual(plan.reason, "auto-local-workspace")

    def test_auto_isolates_only_for_a_declared_reason(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.AUTO,
            is_git_repository=True,
            isolation=WorkspaceIsolationRequest(parallel=True),
        )

        self.assertTrue(plan.isolated)
        self.assertEqual(plan.reason, "auto-isolation:parallel")

    def test_auto_ignores_isolation_when_the_root_is_not_a_repository(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.AUTO,
            is_git_repository=False,
            isolation=WorkspaceIsolationRequest(background=True),
        )

        self.assertFalse(plan.isolated)
        self.assertEqual(
            plan.reason, "auto-isolation-requires-git-repository"
        )

    def test_managed_mode_requires_a_repository(self) -> None:
        isolated = resolve_workspace_plan(
            WorkspaceMode.MANAGED, is_git_repository=True
        )
        local = resolve_workspace_plan(
            WorkspaceMode.MANAGED, is_git_repository=False
        )

        self.assertTrue(isolated.isolated)
        self.assertEqual(isolated.reason, "managed-mode")
        self.assertFalse(local.isolated)
        self.assertEqual(local.reason, "managed-requires-git-repository")


class ExplicitIsolationTests(unittest.TestCase):
    def test_an_explicit_request_isolates_auto_in_a_repository(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.AUTO,
            is_git_repository=True,
            isolation=EXPLICIT_ISOLATION,
        )

        self.assertTrue(plan.isolated)
        self.assertEqual(plan.reason, "auto-isolation:explicit")

    def test_an_explicit_request_cannot_overrule_direct_mode(self) -> None:
        plan = resolve_workspace_plan(
            WorkspaceMode.DIRECT,
            is_git_repository=True,
            isolation=EXPLICIT_ISOLATION,
        )

        self.assertFalse(plan.isolated)
        self.assertEqual(plan.reason, "direct-mode")


class AmbientIsolationScopeTests(unittest.TestCase):
    def test_no_scope_declares_no_reason(self) -> None:
        self.assertIs(task_isolation_request(), NO_ISOLATION)

    def test_the_explicit_scope_asks_for_explicit_isolation(self) -> None:
        with isolated_tasks("explicit"):
            request = task_isolation_request()

        self.assertTrue(request.explicit)
        self.assertEqual(request.reason(), "explicit")

    def test_the_background_scope_asks_for_a_delegated_workspace(self) -> None:
        with isolated_tasks("background"):
            request = task_isolation_request()

        self.assertTrue(request.background)
        self.assertEqual(request.reason(), "background")

    def test_the_scope_is_restored_after_the_block(self) -> None:
        with isolated_tasks("background"):
            with isolated_tasks("explicit"):
                self.assertEqual(task_isolation_request().reason(), "explicit")
            self.assertEqual(task_isolation_request().reason(), "background")
        self.assertIs(task_isolation_request(), NO_ISOLATION)

    def test_an_unknown_scope_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit, background"):
            request_task_isolation("remote")

        self.assertIs(task_isolation_request(), NO_ISOLATION)


class WorkspaceFlagParsingTests(unittest.TestCase):
    def test_isolated_is_stripped_before_the_command(self) -> None:
        self.assertEqual(
            _split_isolation_option(("--isolated", "ask", "hi")),
            (True, ("ask", "hi")),
        )
        self.assertEqual(
            _split_isolation_option(("ask", "hi")), (False, ("ask", "hi"))
        )

    def test_a_repeated_isolation_flag_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "--isolated"):
            _split_isolation_option(("--isolated", "--isolated"))

    def test_reclaim_is_stripped_before_the_command(self) -> None:
        self.assertEqual(
            _split_reclaim_option(("--reclaim-workspaces",)), (True, ())
        )
        self.assertEqual(_split_reclaim_option(("ask",)), (False, ("ask",)))

    def test_a_repeated_reclaim_flag_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "--reclaim-workspaces"):
            _split_reclaim_option(("--reclaim-workspaces", "--reclaim-workspaces"))


class PlanWorkspaceLazinessTests(unittest.TestCase):
    def test_local_plans_never_evaluate_the_repository_probe(self) -> None:
        probe = Mock(side_effect=AssertionError("probe must stay lazy"))

        for mode, isolation in (
            (WorkspaceMode.DIRECT, NO_ISOLATION),
            (WorkspaceMode.AUTO, NO_ISOLATION),
        ):
            with self.subTest(mode=mode):
                plan = plan_workspace(probe, mode=mode, isolation=isolation)
                self.assertFalse(plan.isolated)

        probe.assert_not_called()

    def test_managed_mode_evaluates_the_probe_once(self) -> None:
        probe = Mock(return_value=True)

        plan = plan_workspace(probe, mode=WorkspaceMode.MANAGED)

        self.assertTrue(plan.isolated)
        self.assertEqual(probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
