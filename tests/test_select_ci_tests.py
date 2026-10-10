from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import select_ci_tests as selector
from scripts.run_tests import run_test_suites


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("test_example.py", "test_second.py", "test_continuity_host.py"):
            self.make_file(f"tests/{name}")
        self.make_file("src/code_agent/evaluation/tests/test_metrics.py")
        self.make_file("src/code_agent/core/tests/test_engine.py")

    def make_file(self, name):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")

    def test_documents_are_explicit_and_unknown_markdown_is_full(self):
        for name in ("README.md", "docs/guide.md", "AGENTS.python.md", "src/code_agent/core/AGENTS.md"):
            with self.subTest(name=name):
                self.assertEqual(selector.select_plan([name], self.root)["mode"], "docs")
        for name in ("src/code_agent/providers/prompt.md", "skills/prompt.md", "template.md"):
            with self.subTest(name=name):
                self.assertEqual(selector.select_plan([name], self.root)["mode"], "full")

    def test_root_modules_remain_individual_and_deduplicated(self):
        plan = selector.select_plan(["tests/test_example.py", "tests/test_second.py", "tests/test_example.py"], self.root)
        self.assertEqual(plan["targets"], [{"suite": "tests", "pattern": "test_example.py"},
                                          {"suite": "tests", "pattern": "test_second.py"}])

    def test_feature_tests_only_select_feature_even_for_shared_core(self):
        plan = selector.select_plan(["src/code_agent/core/tests/test_engine.py"], self.root)
        self.assertEqual(plan["targets"], [{"suite": "src/code_agent/core/tests", "pattern": None}])

    def test_local_feature_includes_explicit_integration(self):
        plan = selector.select_plan(["src/code_agent/evaluation/long_context_metrics.py"], self.root)
        self.assertEqual(plan["targets"], [{"suite": "src/code_agent/evaluation/tests", "pattern": None},
                                          {"suite": "tests", "pattern": "test_continuity_host.py"}])

    def test_shared_interfaces_dependencies_and_unknown_files_are_full(self):
        for name in ("src/code_agent/core/engine.py", "src/code_agent/evaluation/models.py",
                     "chaos_agent/app.py", "pyproject.toml", "uv.lock", ".github/workflows/ci.yml",
                     "scripts/select_ci_tests.py", "tests/helper.py", "new_file.py"):
            with self.subTest(name=name):
                self.assertEqual(selector.select_plan([name], self.root)["mode"], "full")

    def test_deleted_test_and_missing_neighbor_are_full(self):
        self.assertEqual(selector.select_plan(["tests/test_deleted.py"], self.root)["mode"], "full")
        (self.root / "tests/test_continuity_host.py").unlink()
        self.assertEqual(selector.select_plan(["src/code_agent/evaluation/long_context_metrics.py"], self.root)["mode"], "full")

    def test_real_cross_suite_helpers_require_full_scope(self):
        self.make_file("src/code_agent/interfaces/tests/test_command_navigation.py")
        (self.root / "tests/test_example.py").write_text(
            "from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app\n", encoding="utf-8")
        for name in ("src/code_agent/interfaces/tests/test_command_navigation.py",
                     "src/code_agent/core/tests/_engine_support.py"):
            with self.subTest(name=name):
                self.assertEqual(selector.select_plan([name], self.root)["mode"], "full")

    def test_root_shared_helpers_support_qualified_and_bare_imports(self):
        for module, statement in (
            ("test_tui_repair_integration", "from tests.test_tui_repair_integration import make_app\n"),
            ("test_tool_schemas", "import test_tool_schemas\n"),
        ):
            with self.subTest(module=module):
                self.make_file(f"tests/{module}.py")
                (self.root / "tests/test_example.py").write_text(statement, encoding="utf-8")
                self.assertEqual(selector.select_plan([f"tests/{module}.py"], self.root)["mode"], "full")

    def test_push_and_pr_compare_checkout_not_untrusted_shell_text(self):
        self.assertEqual(selector.event_refs({"before": "base", "after": "head"}, "push"), ("base", "head"))
        self.assertEqual(selector.event_refs({"pull_request": {"base": {"sha": "base"}}}, "pull_request"), ("base", "HEAD"))
        with self.assertRaises(ValueError):
            selector.event_refs({"deleted": True}, "push")

    def test_missing_zero_or_failed_baseline_is_full(self):
        for base in ("", "0" * 40):
            with self.subTest(base=base):
                self.assertEqual(selector.plan_from_git(self.root, base, "HEAD")["mode"], "full")
        with mock.patch.object(selector, "changed_paths", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertEqual(selector.plan_from_git(self.root, "base", "HEAD")["mode"], "full")

    def test_nul_rename_and_delete_records_keep_both_sides(self):
        output = b"R100\0docs/old name.md\0src/code_agent/core/new.py\0D\0tests/test_deleted.py\0"
        with mock.patch.object(selector.subprocess, "run", side_effect=[
            SimpleNamespace(stdout=b"a" * 40 + b"\n"), SimpleNamespace(stdout=b"b" * 40 + b"\n"),
            SimpleNamespace(stdout=b"b" * 40 + b"\n"),
            SimpleNamespace(stdout=output),
        ]) as git:
            self.assertEqual(selector.changed_paths(self.root, "base", "HEAD"),
                             ["docs/old name.md", "src/code_agent/core/new.py", "tests/test_deleted.py"])
            self.assertEqual(git.call_args.args[0][-3:], ["a" * 40, "b" * 40, "--"])

    def test_full_execution_reuses_split_runner_without_running_tests(self):
        with mock.patch.object(selector, "discover_test_suites", return_value=[self.root / "tests"]), \
                mock.patch.object(selector, "run_test_suites", return_value=0) as runner:
            self.assertEqual(selector.execute_plan(self.root, selector.full_plan("unknown"), 600), 0)
            runner.assert_called_once_with(self.root, [self.root / "tests"], 600, split_root_modules=selector.os.name == "nt")

    def test_requested_head_must_match_actual_checkout(self):
        with mock.patch.object(selector.subprocess, "run", side_effect=[
            SimpleNamespace(stdout=b"a" * 40), SimpleNamespace(stdout=b"b" * 40),
            SimpleNamespace(stdout=b"c" * 40),
        ]):
            with self.assertRaisesRegex(ValueError, "differs from checkout"):
                selector.changed_paths(self.root, "base", "other")

    def test_dirty_checkout_cannot_claim_narrow_scope(self):
        with mock.patch.object(selector, "changed_paths", return_value=["tests/test_example.py"]), \
                mock.patch.object(selector.subprocess, "run", return_value=SimpleNamespace(stdout=b" M production.py\0")):
            plan = selector.plan_from_git(self.root, "base", "HEAD")
            self.assertEqual(plan["mode"], "full")
            self.assertIn("uncommitted", plan["reason"])

    def test_plan_only_never_executes(self):
        with mock.patch.object(selector, "plan_from_git", return_value=selector.full_plan("missing")), \
                mock.patch.object(selector, "execute_plan") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(selector.main(["--base", "base", "--head", "HEAD", "--plan"]), 0)
            execute.assert_not_called()

    def test_selected_modules_reuse_supervisor_and_continue_after_failure(self):
        patterns = ["test_example.py", "test_second.py"]
        with mock.patch("scripts.suite_process.run_supervised_suite", side_effect=[
            SimpleNamespace(returncode=1), SimpleNamespace(returncode=0),
        ]) as supervised, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_test_suites(self.root, [self.root / "tests"] * 2, test_patterns=patterns), 1)
            self.assertEqual([call.kwargs["pattern"] for call in supervised.call_args_list], patterns)

    def test_cleanup_failure_stops_and_identifies_unrun_modules(self):
        output = io.StringIO()
        with mock.patch("scripts.suite_process.run_supervised_suite", side_effect=RuntimeError("cleanup")) as supervised, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_test_suites(self.root, [self.root / "tests"] * 2,
                                            test_patterns=["test_example.py", "test_second.py"]), 2)
        self.assertEqual(supervised.call_count, 1)
        summary = json.loads(output.getvalue().split("CHAOS_TEST_SUMMARY ", 1)[1])
        self.assertEqual(summary["suites"][0]["pattern"], "test_example.py")
        self.assertEqual(summary["unrun_targets"], [{"suite": "tests", "pattern": "test_second.py"}])

    def test_ci_has_always_on_daily_job_and_manual_compatibility(self):
        # Check the essential safety wiring without a YAML dependency in test environments.
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        daily = workflow.split("  daily:\n", 1)[1].split("  windows:\n", 1)[0]
        self.assertIn("if: github.event_name != 'workflow_dispatch' || !inputs.compatibility", daily)
        self.assertIn("fetch-depth: 0", daily)
        self.assertIn("python-version-file: .python-version", daily)
        self.assertIn("scripts/select_ci_tests.py", daily)
        self.assertEqual(workflow.count("if: github.event_name == 'workflow_dispatch' && inputs.compatibility"), 2)


if __name__ == "__main__":
    unittest.main()
