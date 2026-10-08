"""Opt-in module supervision, preserving one logical integration suite."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.suite_manifest import validate_ids, validate_manifest

COUNT_FIELDS = ("discovered", "run", "skipped", "failures", "errors",
                "unexpected_successes", "expected_failures")


def validate_counts(value, discovered: int, executed: int) -> dict:
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0
                                          for key in COUNT_FIELDS):
        raise RuntimeError("missing or invalid group result counts")
    if value["discovered"] != discovered or value["run"] != executed:
        raise RuntimeError("group result counts differ from coverage")
    return {key: value[key] for key in COUNT_FIELDS}


def run_grouped_suite(root: Path, suite: Path, timeout: float):
    from scripts.suite_process import run_supervised_suite

    groups = []
    preflight = run_supervised_suite(root, suite, timeout, discovery_only=True)
    if preflight.returncode:
        return SimpleNamespace(returncode=preflight.returncode, test_counts=None,
                               groups=[], preflight_exit=preflight.returncode,
                               unrun_groups=["discovery unavailable"], coverage_complete=False)
    manifest = validate_manifest(preflight.test_coverage)
    sources = manifest["sources"]
    seen_discovered, seen_run = set(), set()
    code, stopped = 0, False
    for index, (source, expected) in enumerate(sources.items()):
        print(f"--- {suite.as_posix()}/{source} ---", flush=True)
        try:
            completed = run_supervised_suite(root, suite, timeout,
                                            pattern=Path(source).name, coverage=True)
        except Exception as error:
            groups.append({"source": source, "exit_code": 2, "counts": None,
                           "infrastructure_error": type(error).__name__})
            code, stopped = code or 2, True
            break
        record = {"source": source, "exit_code": completed.returncode,
                  "counts": None}
        try:
            coverage = validate_manifest(completed.test_coverage)
            discovered = coverage["discovered_ids"]
            if set(discovered) != set(expected) or set(coverage["sources"]) != {source}:
                raise RuntimeError("module discovery differs from preflight")
            if seen_discovered.intersection(discovered):
                raise RuntimeError("duplicate discovery across groups")
            seen_discovered.update(discovered)
            if completed.test_counts is not None:
                executed = validate_ids(coverage.get("run_ids"), allow_empty=True)
                if not set(executed).issubset(expected) or seen_run.intersection(executed):
                    raise RuntimeError("unexpected or repeated executed IDs")
                record["counts"] = validate_counts(completed.test_counts, len(discovered), len(executed))
                if not completed.returncode and any(record["counts"][key] for key in
                                                    ("failures", "errors", "unexpected_successes")):
                    raise RuntimeError("successful exit contradicts failure counts")
                seen_run.update(executed)
                record["executed_ids"] = executed
                record["execution_complete"] = set(executed) == set(expected)
                if not completed.returncode and not record["execution_complete"]:
                    raise RuntimeError("successful group omitted execution IDs")
            else:
                record["execution_complete"] = None
                if not completed.returncode:
                    raise RuntimeError("successful group omitted result counts")
            record["discovered_ids"] = discovered
            record["strict_id_exceptions"] = coverage.get("strict_id_exceptions", [])
        except (RuntimeError, KeyError, TypeError) as error:
            record["counts"] = None
            record["coverage_error"] = str(error)
            record["exit_code"] = record["exit_code"] or 2
        groups.append(record)
        code = code or record["exit_code"]
    complete = seen_discovered == set(manifest["discovered_ids"])
    if not complete:
        code = code or 2
    counts = [record["counts"] for record in groups]
    totals = None
    if counts and all(isinstance(item, dict) for item in counts):
        totals = {key: sum(item[key] for item in counts) for key in counts[0]}
    return SimpleNamespace(returncode=code, test_counts=totals, groups=groups,
                           preflight_exit=0, coverage_complete=complete,
                           expected_discovered=len(manifest["discovered_ids"]),
                           executed_unique=(len(seen_run) if all(
                               record.get("execution_complete") is not None
                               and "coverage_error" not in record for record in groups) else None),
                           unrun_groups=list(sources)[len(groups):],
                           infrastructure_error="group cleanup/control failed" if stopped else None)
