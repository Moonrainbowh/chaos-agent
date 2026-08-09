from __future__ import annotations

import argparse
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


def run_suite(root: Path, start_dir: str, pattern: str) -> int:
    root = Path(root).resolve()
    relative, suite = _resolve_suite(root, start_dir)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    program = unittest.main(
        module=None,
        argv=["unittest", "discover", "-s", str(suite), "-p", pattern],
        testRunner=StructuredRunner,
        exit=False,
    )
    result = program.result
    if not isinstance(result, StructuredTextResult):
        raise RuntimeError("structured unittest result is unavailable")
    returncode = 0 if result.wasSuccessful() else 1
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
    options = parser.parse_args(arguments)
    try:
        return run_suite(repository_root(), options.start_dir, options.pattern)
    except (RuntimeError, ValueError) as error:
        print(f"test suite runner error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
