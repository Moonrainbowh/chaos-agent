"""Exact discovery ownership and execution coverage for opt-in module groups."""
from __future__ import annotations

import json
import hashlib
import re
import sys
import unittest
from pathlib import Path


def tests_in(suite):
    if isinstance(suite, unittest.TestSuite):
        for item in suite:
            yield from tests_in(item)
    else:
        yield suite


def discovery_manifest(suite, start: Path) -> dict:
    sources: dict[str, list[str]] = {}
    ids = []
    exceptions = []
    for test in tests_in(suite):
        if not isinstance(test, unittest.TestCase):
            raise RuntimeError("discovery produced a non-TestCase")
        if type(test).__name__ == "_FailedTest":
            raise RuntimeError("module import failed during grouped discovery")
        identifier, reason = coverage_test_id(test)
        if reason:
            exceptions.append({"coverage_id": identifier, "reason": reason})
        if identifier in ids:
            raise RuntimeError("duplicate discovered test ID")
        module = sys.modules.get(type(test).__module__)
        filename = getattr(module, "__file__", None)
        if not filename:
            raise RuntimeError("discovered test has no source file")
        try:
            source = Path(filename).resolve().relative_to(start.resolve()).as_posix()
        except ValueError as error:
            raise RuntimeError("discovered test source escapes suite") from error
        if not Path(source).name.startswith("test_") or not source.endswith(".py"):
            raise RuntimeError("discovered test source is not a test module")
        ids.append(identifier)
        sources.setdefault(source, []).append(identifier)
    if not ids:
        raise RuntimeError("grouped discovery found zero tests")
    return {"discovered_ids": ids, "sources": dict(sorted(sources.items())),
            "run_ids": [], "strict_id_exceptions": exceptions}


def coverage_test_id(test) -> tuple[str, str | None]:
    """Keep strict diagnostic IDs; opaque coverage keys expose no rejected text."""
    from scripts.run_test_suite import strict_test_id
    identifier = strict_test_id(test)
    if identifier != "unittest.redacted":
        validate_ids([identifier])
        return identifier, None
    values = (type(test).__module__, type(test).__qualname__,
              getattr(test, "_testMethodName", None))
    if not isinstance(test, unittest.TestCase) or not all(type(item) is str for item in values):
        raise RuntimeError("unsupported opaque coverage identity")
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=True).encode("utf-8")).hexdigest()
    return f"coverage.h{digest[:32]}.h{digest[32:]}", "strict_identifier_redacted"


def write_coverage(path: str | None, value: dict) -> None:
    if path:
        Path(path).write_text(json.dumps(value), encoding="utf-8")


def validate_ids(ids: object, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(ids, list) or (not ids and not allow_empty):
        raise RuntimeError("empty or invalid grouped IDs")
    for identifier in ids:
        if (not isinstance(identifier, str) or len(identifier) > 240
                or identifier in {"unittest.redacted", "unittest.fixture_error"}
                or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", part, re.ASCII)
                           for part in identifier.split("."))):
            raise RuntimeError("unsafe grouped test identifier")
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate grouped test IDs")
    return ids


def validate_manifest(value: object) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError("missing grouped discovery coverage")
    ids, sources = value.get("discovered_ids"), value.get("sources")
    validate_ids(ids)
    if not isinstance(sources, dict):
        raise RuntimeError("invalid grouped sources")
    owned = []
    names = set()
    for source, members in sources.items():
        if not isinstance(source, str):
            raise RuntimeError("invalid grouped source path")
        path = Path(source)
        if path.is_absolute() or ".." in path.parts or not path.name.startswith("test_") or path.suffix != ".py":
            raise RuntimeError("invalid grouped source path")
        if path.name in names:
            raise RuntimeError("ambiguous grouped module filename")
        names.add(path.name)
        validate_ids(members)
        owned.extend(members)
    if len(owned) != len(set(owned)) or set(owned) != set(ids):
        raise RuntimeError("group source ID union differs from full discovery")
    exceptions = value.get("strict_id_exceptions", [])
    if not isinstance(exceptions, list):
        raise RuntimeError("invalid strict ID exception list")
    exception_ids = set()
    for exception in exceptions:
        if (not isinstance(exception, dict) or set(exception) != {"coverage_id", "reason"}
                or exception["coverage_id"] not in ids
                or exception["coverage_id"] in exception_ids
                or exception["reason"] != "strict_identifier_redacted"):
            raise RuntimeError("invalid strict ID exception record")
        exception_ids.add(exception["coverage_id"])
    return value
