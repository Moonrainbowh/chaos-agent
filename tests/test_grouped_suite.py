from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from scripts.grouped_suite import run_grouped_suite
from scripts.run_tests import run_test_suites
from scripts.suite_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
COUNTS = {"discovered": 1, "run": 1, "skipped": 0, "failures": 0,
          "errors": 0, "unexpected_successes": 0, "expected_failures": 0}


def coverage(source="test_a.py", identifier="test_a.Case.test_a"):
    return {"discovered_ids": [identifier], "sources": {source: [identifier]},
            "run_ids": [identifier]}


def completed(value=None, code=0, counts=COUNTS):
    return SimpleNamespace(returncode=code, test_counts=counts,
                           test_coverage=value or coverage())


class GroupedSuiteTests(unittest.TestCase):
    def test_manifest_rejects_empty_duplicate_missing_and_ambiguous_sources(self):
        values = [coverage(), coverage(), coverage(), coverage()]
        values[0]["discovered_ids"] = []
        values[1]["discovered_ids"] *= 2
        values[2]["sources"] = {"test_a.py": ["missing.Case.test_x"]}
        values[3]["sources"]["nested/test_a.py"] = ["missing.Case.test_x"]
        for value in values:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_manifest(value)

    def test_manifest_rejects_unhashable_or_unsafe_ids_without_echoing_values(self):
        for identifier in (["unhashable"], "SECRET\nunsafe", "x" * 241,
                           "unittest.redacted", "unittest.fixture_error"):
            value = coverage()
            value["sources"]["test_a.py"] = [identifier]
            with self.subTest(kind=type(identifier).__name__):
                with self.assertRaises(RuntimeError) as caught:
                    validate_manifest(value)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_real_overridden_id_is_ignored_in_coverage(self):
        with tempfile.TemporaryDirectory(prefix="group-id-", dir=ROOT) as directory:
            suite = Path(directory)
            (suite / "test_a.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n"
                " def id(self): return 'PRIVATE_SENTINEL_SECRET' * 100\n"
                " def test_one(self): pass\n", encoding="utf-8")
            result = run_grouped_suite(ROOT, suite.relative_to(ROOT), 10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.groups[0]["discovered_ids"], ["test_a.Case.test_one"])
        self.assertEqual(result.groups[0]["executed_ids"], ["test_a.Case.test_one"])
        self.assertNotIn("PRIVATE_SENTINEL_SECRET", repr(result))

    def test_real_long_method_uses_bounded_opaque_coverage_identity(self):
        with tempfile.TemporaryDirectory(prefix="group-long-id-", dir=ROOT) as directory:
            suite = Path(directory)
            (suite / "test_a.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n def test_" + "x" * 100
                + "(self): pass\n", encoding="utf-8")
            result = run_grouped_suite(ROOT, suite.relative_to(ROOT), 10)
        self.assertEqual(result.returncode, 0)
        group = result.groups[0]
        self.assertEqual(group["discovered_ids"], group["executed_ids"])
        self.assertTrue(group["discovered_ids"][0].startswith("coverage.h"))
        self.assertEqual(group["strict_id_exceptions"], [{
            "coverage_id": group["discovered_ids"][0], "reason": "strict_identifier_redacted"}])
        self.assertNotIn("x" * 100, repr(result))

    def test_missing_or_malformed_counts_cannot_manufacture_success(self):
        for counts in (None, dict(COUNTS, run=True), dict(COUNTS, skipped=-1),
                       dict(COUNTS, discovered=999), {"run": 1},
                       dict(COUNTS, errors=1), dict(COUNTS, failures=1),
                       dict(COUNTS, unexpected_successes=1)):
            with self.subTest(counts=counts), patch(
                "scripts.suite_process.run_supervised_suite",
                side_effect=[completed(), completed(counts=counts)],
            ), contextlib.redirect_stdout(io.StringIO()):
                result = run_grouped_suite(ROOT, Path("tests"), 600)
            self.assertEqual(result.returncode, 2)
            self.assertIsNone(result.test_counts)
            self.assertIsNone(result.groups[0]["counts"])

    def test_real_nested_supervision_does_not_inherit_coverage_ipc(self):
        from scripts.suite_process import run_supervised_suite
        with tempfile.TemporaryDirectory(prefix="group-ipc-", dir=ROOT) as directory:
            suite = Path(directory)
            sentinel = suite / "parent.json"
            sentinel.write_text("PARENT_KEEP", encoding="utf-8")
            (suite / "test_a.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n def test_one(self): pass\n",
                encoding="utf-8")
            with patch.dict(os.environ, {"CHAOS_TEST_COVERAGE": str(sentinel)}):
                result = run_supervised_suite(ROOT, suite.relative_to(ROOT), 10)
            self.assertEqual(sentinel.read_text(), "PARENT_KEEP")
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.test_coverage)

    def test_group_discovery_mismatch_or_duplicate_execution_is_failure(self):
        for value in (coverage("test_b.py", "test_b.Case.test_b"), coverage()):
            if "test_a.py" in value["sources"]:
                value["run_ids"] *= 2
            with self.subTest(value=value), patch(
                "scripts.suite_process.run_supervised_suite",
                side_effect=[completed(), completed(value)],
            ), contextlib.redirect_stdout(io.StringIO()):
                result = run_grouped_suite(ROOT, Path("tests"), 600)
            self.assertEqual(result.returncode, 2)
            self.assertIn("coverage_error", result.groups[0])

    def test_invalid_preflight_stops_before_execution_and_lists_later_scope(self):
        value = coverage()
        value["discovered_ids"] *= 2
        stream = io.StringIO()
        with patch("scripts.suite_process.run_supervised_suite",
                   return_value=completed(value)) as run, contextlib.redirect_stdout(stream):
            code = run_test_suites(ROOT, (ROOT / "tests", ROOT / "later"),
                                   split_root_modules=True)
        self.assertEqual(code, 2)
        self.assertEqual(run.call_count, 1)
        summary = json.loads(stream.getvalue().split("CHAOS_TEST_SUMMARY ")[-1])
        self.assertEqual(summary["unrun_suites"], ["later"])
        self.assertIn("infrastructure_error", summary["suites"][0])

    def test_empty_group_fails_and_later_group_still_runs(self):
        value = coverage()
        value["sources"]["test_b.py"] = ["test_b.Case.test_b"]
        value["discovered_ids"].append("test_b.Case.test_b")
        empty = {"discovered_ids": [], "sources": {}, "run_ids": []}
        counts = dict(COUNTS, discovered=0, run=0)
        with patch("scripts.suite_process.run_supervised_suite", side_effect=[
            completed(value), completed(empty, 2, counts),
            completed(coverage("test_b.py", "test_b.Case.test_b")),
        ]), contextlib.redirect_stdout(io.StringIO()):
            result = run_grouped_suite(ROOT, Path("tests"), 600)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.groups), 2)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.groups[-1]["exit_code"], 0)

    def test_fixture_failure_preserves_discovered_run_difference(self):
        value = coverage()
        value["run_ids"] = []
        counts = dict(COUNTS, run=0, errors=1)
        with patch("scripts.suite_process.run_supervised_suite",
                   side_effect=[completed(), completed(value, 1, counts)]), \
                contextlib.redirect_stdout(io.StringIO()):
            result = run_grouped_suite(ROOT, Path("tests"), 600)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.test_counts["discovered"], 1)
        self.assertEqual(result.test_counts["run"], 0)
        self.assertFalse(result.groups[0]["execution_complete"])

    def test_cleanup_failure_stops_groups_and_later_logical_suites(self):
        value = coverage()
        value["sources"]["test_b.py"] = ["test_b.Case.test_b"]
        value["discovered_ids"].append("test_b.Case.test_b")
        stream = io.StringIO()
        with patch("scripts.suite_process.run_supervised_suite",
                   side_effect=[completed(value), RuntimeError("unsafe cleanup")]) as run, \
                contextlib.redirect_stdout(stream):
            code = run_test_suites(ROOT, (ROOT / "tests", ROOT / "later"),
                                   split_root_modules=True)
        self.assertEqual(code, 2)
        self.assertEqual(run.call_count, 2)
        summary = json.loads(stream.getvalue().split("CHAOS_TEST_SUMMARY ")[-1])
        self.assertEqual(summary["unrun_suites"], ["later"])
        self.assertEqual(summary["suites"][0]["unrun_groups"], ["test_b.py"])
        self.assertIsNone(summary["totals"])

    def test_default_does_not_preflight_or_split(self):
        with patch("scripts.suite_process.run_supervised_suite",
                   return_value=completed()) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_test_suites(ROOT, (ROOT / "tests",)), 0)
        run.assert_called_once_with(ROOT, Path("tests"), 600)

    def test_real_groups_preserve_fixtures_failures_and_skips(self):
        with tempfile.TemporaryDirectory(prefix="group-probe-", dir=ROOT) as directory:
            suite = Path(directory)
            (suite / "test_a.py").write_text(
                "import unittest\nready=False\ndef setUpModule():\n global ready; ready=True\n"
                "class Case(unittest.TestCase):\n"
                " def test_one(self): self.assertTrue(ready)\n"
                " @unittest.skip('fixture skip')\n def test_two(self): pass\n", encoding="utf-8")
            (suite / "test_b.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n"
                " def test_fail(self): self.fail('intentional')\n", encoding="utf-8")
            (suite / "test_c.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n"
                " def test_after(self): pass\n", encoding="utf-8")
            result = run_grouped_suite(ROOT, suite.relative_to(ROOT), 10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.groups), 3)
        self.assertEqual(result.test_counts["discovered"], 4)
        self.assertEqual(result.test_counts["run"], 4)
        self.assertEqual(result.test_counts["skipped"], 1)
        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.executed_unique, 4)
        self.assertEqual(result.groups[-1]["exit_code"], 0)

    def test_real_hung_group_cleans_child_and_runs_later_module(self):
        with tempfile.TemporaryDirectory(prefix="group-hang-", dir=ROOT) as directory:
            suite = Path(directory)
            (suite / "test_a.py").write_text(
                "import unittest, subprocess, sys, time\nfrom pathlib import Path\n"
                "class Case(unittest.TestCase):\n def test_hang(self):\n"
                "  child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                "  Path(__file__).with_name('child.pid').write_text(str(child.pid))\n"
                "  time.sleep(60)\n", encoding="utf-8")
            (suite / "test_b.py").write_text(
                "import unittest\nclass Case(unittest.TestCase):\n def test_after(self): pass\n",
                encoding="utf-8")
            result = run_grouped_suite(ROOT, suite.relative_to(ROOT), 3)
            child = int((suite / "child.pid").read_text())
        self.assertEqual(result.returncode, 124)
        self.assertFalse(psutil.pid_exists(child))
        self.assertEqual(result.groups[-1]["exit_code"], 0)
        self.assertTrue(result.coverage_complete)
        self.assertIsNone(result.test_counts)

    def test_empty_real_discovery_is_failure(self):
        with tempfile.TemporaryDirectory(prefix="group-empty-", dir=ROOT) as directory:
            suite = Path(directory)
            (suite / "test_a.py").write_text("import unittest\n", encoding="utf-8")
            result = run_grouped_suite(ROOT, suite.relative_to(ROOT), 10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.groups, [])


if __name__ == "__main__":
    unittest.main()
