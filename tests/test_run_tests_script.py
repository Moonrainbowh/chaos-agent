from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_tests import (  # noqa: E402
    _emit_github_failure,
    discover_test_suites,
    run_test_suites,
)


class TestSuiteDiscoveryTests(unittest.TestCase):
    def test_discovers_sorted_features_and_integration_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_root = root.resolve()
            for relative in (
                "src/code_agent/zeta/tests/test_zeta.py",
                "src/code_agent/alpha/tests/test_alpha.py",
                "tests/test_root.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            suites = discover_test_suites(root)

        self.assertEqual(
            tuple(path.relative_to(canonical_root).as_posix() for path in suites),
            (
                "src/code_agent/alpha/tests",
                "src/code_agent/zeta/tests",
                "tests",
            ),
        )

    def test_rejects_a_feature_without_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/code_agent/alpha").mkdir(parents=True)
            integration = root / "tests/test_root.py"
            integration.parent.mkdir()
            integration.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "src/code_agent/alpha"):
                discover_test_suites(root)

    def test_rejects_nested_only_tests_that_discover_would_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "src/code_agent/alpha/tests/nested/test_alpha.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("", encoding="utf-8")
            integration = root / "tests/test_root.py"
            integration.parent.mkdir()
            integration.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "src/code_agent/alpha"):
                discover_test_suites(root)


class TestSuiteDiagnosticsTests(unittest.TestCase):
    def test_ci_failure_annotation_identifies_suite(self) -> None:
        root = Path.cwd()
        suite = root / "tests"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            mock.patch(
                "scripts.run_tests.subprocess.run",
                return_value=SimpleNamespace(returncode=1),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = run_test_suites(root, (suite,))

        self.assertEqual(returncode, 1)
        self.assertIn("test suite failed: tests", stderr.getvalue())
        annotation = stdout.getvalue()
        self.assertIn("::error title=Chaos Agent test suite failed::", annotation)
        self.assertIn("suite=tests", annotation)
        self.assertIn("exit_code=1", annotation)

    def test_non_ci_failure_preserves_returncode_without_annotation(self) -> None:
        root = Path.cwd()
        suite = root / "tests"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}),
            mock.patch(
                "scripts.run_tests.subprocess.run",
                return_value=SimpleNamespace(returncode=7),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = run_test_suites(root, (suite,))

        self.assertEqual(returncode, 7)
        self.assertNotIn("::error", stdout.getvalue())
        self.assertIn("test suite failed: tests", stderr.getvalue())

    def test_annotation_escapes_newlines_and_percent_signs(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            _emit_github_failure(
                Path("tests\n::error title=pwned::bad%"),
                1,
            )

        annotation = stdout.getvalue()
        self.assertEqual(len(annotation.splitlines()), 1)
        self.assertIn("tests%0A::error title=pwned::bad%25", annotation)

if __name__ == "__main__":
    unittest.main()
