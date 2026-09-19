from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_test_suite import (
    StructuredTextResult,
    _emit_github_failure,
    run_suite,
    strict_test_id,
)


def _failure_case() -> type[unittest.TestCase]:
    class FailureCase(unittest.TestCase):
        def test_failure(self) -> None:
            self.fail("api_token=top-secret")

    FailureCase.__qualname__ = "_FailureCase"
    return FailureCase


def _error_case() -> type[unittest.TestCase]:
    class ErrorCase(unittest.TestCase):
        def test_error(self) -> None:
            raise RuntimeError("password=top-secret")

    ErrorCase.__qualname__ = "_ErrorCase"
    return ErrorCase


def _subtest_case() -> type[unittest.TestCase]:
    class SubTestCase(unittest.TestCase):
        def test_subtest(self) -> None:
            with self.subTest(api_token="top-secret"):
                self.fail("private subtest value")

    SubTestCase.__qualname__ = "_SubTestCase"
    return SubTestCase


def _mutation_case() -> type[unittest.TestCase]:
    class MutationCase(unittest.TestCase):
        def test_mutation(self) -> None:
            self._testMethodName = "api_token_top_secret"
            self.fail("private mutation value")

    MutationCase.__qualname__ = "_MutationCase"
    return MutationCase


def _unexpected_success_case() -> type[unittest.TestCase]:
    class UnexpectedSuccessCase(unittest.TestCase):
        @unittest.expectedFailure
        def test_unexpected_success(self) -> None:
            pass

    UnexpectedSuccessCase.__qualname__ = "_UnexpectedSuccessCase"
    return UnexpectedSuccessCase


def _run_cases(*cases: unittest.TestCase) -> StructuredTextResult:
    runner = unittest.TextTestRunner(
        stream=io.StringIO(),
        verbosity=0,
        resultclass=StructuredTextResult,
    )
    result = runner.run(unittest.TestSuite(cases))
    if not isinstance(result, StructuredTextResult):
        raise AssertionError("expected a structured result")
    return result


class StructuredIdentifierTests(unittest.TestCase):
    def test_identifier_uses_type_and_method_metadata(self) -> None:
        case = _failure_case()("test_failure")
        identifier = strict_test_id(case)

        self.assertTrue(identifier.endswith("._FailureCase.test_failure"))

    def test_invalid_metadata_is_redacted_without_echoing_it(self) -> None:
        case = _failure_case()("test_failure")
        case._testMethodName = "秘密\napi_token"

        self.assertEqual(strict_test_id(case), "unittest.redacted")

    def test_fixture_error_uses_a_fixed_identifier(self) -> None:
        holder = unittest.suite._ErrorHolder("setUpClass (private.SecretCase)")

        self.assertEqual(strict_test_id(holder), "unittest.fixture_error")


class SourceTreeImportTests(unittest.TestCase):
    def test_source_tree_precedes_an_installed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "src").mkdir()
            (root / "tests").mkdir()
            result = StructuredTextResult(io.StringIO(), False, 1)
            with (
                mock.patch.object(
                    sys,
                    "path",
                    ["installed-packages", str(root), str(root / "src")],
                ),
                mock.patch(
                    "scripts.run_test_suite.unittest.main",
                    return_value=mock.Mock(result=result),
                ),
            ):
                returncode = run_suite(root, "tests", "test_*.py")
                active_path = tuple(sys.path)

        self.assertEqual(returncode, 0)
        self.assertEqual(active_path[:2], (str(root / "src"), str(root)))
        self.assertEqual(active_path[2], "installed-packages")


class StructuredResultTests(unittest.TestCase):
    def test_failure_kinds_do_not_capture_runtime_values(self) -> None:
        result = _run_cases(
            _failure_case()("test_failure"),
            _error_case()("test_error"),
            _subtest_case()("test_subtest"),
            _mutation_case()("test_mutation"),
            _unexpected_success_case()("test_unexpected_success"),
        )

        rendered = repr(result.records)
        self.assertIn("failure", rendered)
        self.assertIn("error", rendered)
        self.assertIn("subtest", rendered)
        self.assertIn("unexpected_success", rendered)
        self.assertIn("._MutationCase.test_mutation", rendered)
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("api_token_top_secret", rendered)

    def test_nested_result_does_not_overwrite_supervisor_progress(self) -> None:
        with mock.patch.dict(os.environ, {"CHAOS_TEST_PROGRESS": "unused-progress.json"}), mock.patch(
            "scripts.run_test_suite.Path.write_text"
        ) as write:
            _run_cases(_failure_case()("test_failure"))
        write.assert_not_called()

    def test_duplicate_records_are_collapsed(self) -> None:
        failure_case = _failure_case()
        result = _run_cases(
            failure_case("test_failure"),
            failure_case("test_failure"),
        )

        self.assertEqual(len(result.records), 1)
        self.assertFalse(result.truncated)

    def test_records_are_bounded(self) -> None:
        methods = {}
        for index in range(9):
            def failing(self: unittest.TestCase, marker: int = index) -> None:
                self.fail(f"private-{marker}")

            methods[f"test_failure_{index}"] = failing
        many_failures = type("ManyFailures", (unittest.TestCase,), methods)
        many_failures.__module__ = __name__
        cases = tuple(many_failures(name) for name in sorted(methods))

        result = _run_cases(*cases)

        self.assertEqual(len(result.records), 8)
        self.assertTrue(result.truncated)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            _emit_github_failure(Path("tests"), 1, result)
        self.assertIn("truncated=true", stdout.getvalue())

    def test_annotation_contains_only_structured_identifiers(self) -> None:
        result = _run_cases(_failure_case()("test_failure"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            _emit_github_failure(
                Path("tests\n::error title=pwned::bad%"),
                1,
                result,
            )

        annotation = stdout.getvalue()
        self.assertEqual(len(annotation.splitlines()), 1)
        self.assertIn("._FailureCase.test_failure", annotation)
        self.assertIn("%0A::error title=pwned::bad%25", annotation)
        self.assertNotIn("top-secret", annotation)


if __name__ == "__main__":
    unittest.main()
