from __future__ import annotations

import argparse
import ast
import faulthandler
import json
import os
import re
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path


_ID_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}", re.ASCII)
_MAX_IDENTIFIER_LENGTH = 240
_MAX_RECORDS = 8


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def prioritize_source_tree(root: Path) -> None:
    """Prefer the checkout for this process and any nested test subprocesses."""
    root = Path(root).resolve()
    preferred = [str(root / "src"), str(root)]
    sys.path[:] = preferred + [entry for entry in sys.path if entry not in preferred]
    inherited = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        preferred + [entry for entry in inherited if entry and entry not in preferred]
    )


def strict_test_id(test: object) -> str:
    test_type = type(test)
    if test_type.__module__ == "unittest.suite" and test_type.__name__ == "_ErrorHolder":
        return "unittest.fixture_error"
    if not isinstance(test, unittest.TestCase):
        return "unittest.redacted"
    method = getattr(test, "_testMethodName", None)
    module = test_type.__module__
    qualified_name = test_type.__qualname__
    values = (module, qualified_name, method)
    if not all(
        type(value) is str and len(value) <= _MAX_IDENTIFIER_LENGTH
        for value in values
    ):
        return "unittest.redacted"
    parts = (*module.split("."), *qualified_name.split("."), method)
    if not all(_ID_SEGMENT.fullmatch(part) for part in parts):
        return "unittest.redacted"
    identifier = ".".join(parts)
    if len(identifier) > _MAX_IDENTIFIER_LENGTH:
        return "unittest.redacted"
    return identifier


class StructuredTextResult(unittest.TextTestResult):
    def __init__(self, stream: object, descriptions: bool, verbosity: int) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.records: list[tuple[str, str]] = []
        self.truncated = False
        self._active_ids: dict[int, str] = {}

    def startTest(self, test: object) -> None:
        self._active_ids[id(test)] = strict_test_id(test)
        progress = getattr(self, "progress_path", None)
        if progress:
            Path(progress).write_text(json.dumps({"last_test": self._active_ids[id(test)]}), encoding="utf-8")
        super().startTest(test)

    def stopTest(self, test: object) -> None:
        try:
            super().stopTest(test)
        finally:
            self._active_ids.pop(id(test), None)

    def addFailure(self, test: object, err: object) -> None:
        self._record("failure", test)
        super().addFailure(test, err)

    def addError(self, test: object, err: object) -> None:
        self._record("error", test)
        super().addError(test, err)

    def addSubTest(self, test: object, subtest: object, err: object | None) -> None:
        if err is not None:
            self._record("subtest", test)
        super().addSubTest(test, subtest, err)

    def addUnexpectedSuccess(self, test: object) -> None:
        self._record("unexpected_success", test)
        super().addUnexpectedSuccess(test)

    def _record(self, kind: str, test: object) -> None:
        identifier = self._active_ids.get(id(test))
        if identifier is None:
            identifier = (
                "unittest.fixture_error"
                if strict_test_id(test) == "unittest.fixture_error"
                else "unittest.redacted"
            )
        record = (kind, identifier)
        if record in self.records:
            return
        if len(self.records) >= _MAX_RECORDS:
            self.truncated = True
            return
        self.records.append(record)


class StructuredRunner(unittest.TextTestRunner):
    resultclass = StructuredTextResult

    def _makeResult(self):
        result = super()._makeResult()
        result.progress_path = os.environ.get("CHAOS_TEST_PROGRESS")
        result.discovered = getattr(self, "discovered", 0)
        return result

    def run(self, test):
        self.discovered = test.countTestCases()
        return super().run(test)


def _test_sources(suite: Path, pattern: str):
    """Visit the root and importable subpackages, matching unittest discovery."""
    pending = [suite]
    while pending:
        directory = pending.pop()
        yield from sorted(directory.glob(pattern))
        pending.extend(path for path in sorted(directory.iterdir(), reverse=True)
                       if path.is_dir() and (path / "__init__.py").is_file())


def run_suite(root: Path, start_dir: str, pattern: str) -> int:
    root = Path(root).resolve()
    relative, suite = _resolve_suite(root, start_dir)
    prioritize_source_tree(root)
    # unittest silently ignores module-level pytest functions. Do not accept a
    # partially discovered suite; an explicit load_tests hook may adapt them.
    for path in _test_sources(suite, pattern):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
        functions = [node.name for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if any(name.startswith("test_") for name in functions) and "load_tests" not in functions:
            raise RuntimeError(f"unittest cannot discover module-level tests in {path.relative_to(suite).as_posix()}; use TestCase or load_tests")
    program = unittest.main(
        module=None,
        argv=["unittest", "discover", "-s", str(suite), "-p", pattern],
        testRunner=StructuredRunner,
        testLoader=unittest.TestLoader(),
        exit=False,
    )
    result = program.result
    if not isinstance(result, StructuredTextResult):
        raise RuntimeError("structured unittest result is unavailable")
    returncode = 0 if result.wasSuccessful() else 1
    if not result.testsRun and result.wasSuccessful():
        returncode = 2
    counts = {"discovered": getattr(result, "discovered", result.testsRun),
              "run": result.testsRun, "skipped": len(result.skipped),
              "failures": len(result.failures), "errors": len(result.errors),
              "unexpected_successes": len(result.unexpectedSuccesses),
              "expected_failures": len(result.expectedFailures)}
    print("CHAOS_SUITE_RESULT " + json.dumps({"suite": relative.as_posix(),
          "exit_code": returncode, **counts}, sort_keys=True), flush=True)
    result_path = os.environ.get("CHAOS_TEST_RESULT")
    if result_path and root == repository_root():
        Path(result_path).write_text(json.dumps(counts), encoding="utf-8")
    if not result.testsRun and result.wasSuccessful():
        print(f"test discovery error: {relative.as_posix()} discovered zero tests", file=sys.stderr)
    if returncode and os.environ.get("GITHUB_ACTIONS", "").casefold() == "true":
        _emit_github_failure(relative, returncode, result)
    return returncode


def _resolve_suite(root: Path, start_dir: str) -> tuple[Path, Path]:
    supplied = Path(start_dir)
    if supplied.is_absolute():
        raise ValueError("test suite must be repository-relative")
    suite = (root / supplied).resolve()
    try:
        relative = suite.relative_to(root)
    except ValueError as error:
        raise ValueError("test suite escapes the repository") from error
    if not suite.is_dir():
        raise ValueError(f"test suite is unavailable: {relative.as_posix()}")
    return relative, suite


def _emit_github_failure(
    suite: Path, returncode: int, result: StructuredTextResult
) -> None:
    records = "|".join(f"{kind}:{identifier}" for kind, identifier in result.records)
    details = [f"suite={suite.as_posix()}", f"exit_code={returncode}"]
    details.append(f"tests={records or 'unavailable'}")
    if result.truncated:
        details.append("truncated=true")
    message = _escape_workflow_data("; ".join(details))
    print(f"::error title=Chaos Agent structured test failure::{message}", flush=True)


def _escape_workflow_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one unittest suite with safe CI diagnostics.")
    parser.add_argument("--start-dir", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--supervised", action="store_true")
    parser.add_argument("--timeout", type=float, default=300)
    options = parser.parse_args(arguments)
    if options.supervised:
        if sys.stdin.buffer.read(1) != b"1":
            return 2
        faulthandler.dump_traceback_later(options.timeout)

    try:
        return run_suite(repository_root(), options.start_dir, options.pattern)
    except (RuntimeError, ValueError) as error:
        print(f"test suite runner error: {error}", file=sys.stderr)
        return 2
    finally:
        if options.supervised:
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    raise SystemExit(main())
