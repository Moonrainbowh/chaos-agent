from __future__ import annotations

import argparse
import os
import math
import json
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__:
    from .run_test_suite import prioritize_source_tree
else:
    from run_test_suite import prioritize_source_tree


TEST_PATTERN = "test_*.py"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def discover_test_suites(root: Path) -> tuple[Path, ...]:
    """Return every Feature suite followed by the root integration suite."""
    root = Path(root).resolve()
    feature_root = root / "src" / "code_agent"
    if not feature_root.is_dir():
        raise RuntimeError(f"feature root is unavailable: {feature_root}")

    feature_directories = sorted(
        (
            path
            for path in feature_root.iterdir()
            if path.is_dir()
            and path.name != "__pycache__"
            and not path.name.startswith(".")
        ),
        key=lambda path: path.name.casefold(),
    )
    suites: list[Path] = []
    missing: list[Path] = []
    for feature in feature_directories:
        suite = feature / "tests"
        if not suite.is_dir() or not any(suite.glob(TEST_PATTERN)):
            missing.append(feature.relative_to(root))
            continue
        suites.append(suite)

    remote = root / "chaos_agent" / "remote" / "tests"
    if (root / "chaos_agent" / "remote").is_dir():
        if not remote.is_dir() or not any(remote.glob(TEST_PATTERN)):
            missing.append(remote.relative_to(root))
        else:
            suites.append(remote)
    integration = root / "tests"
    if not integration.is_dir() or not any(integration.glob(TEST_PATTERN)):
        missing.append(integration.relative_to(root))
    else:
        suites.append(integration)

    if missing:
        rendered = ", ".join(path.as_posix() for path in missing)
        raise RuntimeError(f"test suite is missing or empty for: {rendered}")
    return tuple(suites)


def run_test_suites(root: Path, suites: Sequence[Path], suite_timeout: float = 600,
                    *, split_root_modules: bool = False,
                    test_patterns: Sequence[str | None] | None = None) -> int:
    if test_patterns is not None and len(test_patterns) != len(suites):
        raise ValueError("test patterns must match selected suites")
    prioritize_source_tree(root)
    github_actions = os.environ.get("GITHUB_ACTIONS", "").casefold() == "true"
    results = []
    exit_code = 0
    for index, suite in enumerate(suites):
        pattern = test_patterns[index] if test_patterns is not None else None
        relative = suite.relative_to(root)
        print(f"=== {relative.as_posix()} ===", flush=True)
        from scripts.suite_process import run_supervised_suite

        try:
            if split_root_modules and relative == Path("tests") and pattern is None:
                from scripts.grouped_suite import run_grouped_suite
                completed = run_grouped_suite(root, relative, suite_timeout)
            else:
                if pattern is None:
                    completed = run_supervised_suite(root, relative, suite_timeout)
                else:
                    completed = run_supervised_suite(root, relative, suite_timeout, pattern=pattern)
        except Exception as error:
            # A cleanup/control failure is unsafe to continue. Still make the
            # unfinished scope explicit instead of claiming a complete run.
            print(f"test infrastructure error: {relative.as_posix()}: {type(error).__name__}",
                  file=sys.stderr, flush=True)
            results.append({"suite": relative.as_posix(), "exit_code": 2,
                            "counts": None, "infrastructure_error": type(error).__name__})
            if pattern is not None:
                results[-1]["pattern"] = pattern
            exit_code = exit_code or 2
            break
        results.append({"suite": relative.as_posix(), "exit_code": completed.returncode,
                        "counts": getattr(completed, "test_counts", None)})
        if pattern is not None:
            results[-1]["pattern"] = pattern
        if hasattr(completed, "groups"):
            results[-1].update({key: getattr(completed, key, None) for key in (
                "groups", "preflight_exit", "coverage_complete", "expected_discovered",
                "executed_unique", "unrun_groups", "infrastructure_error")})
            if getattr(completed, "infrastructure_error", None):
                exit_code = exit_code or completed.returncode or 2
                break
        if completed.returncode != 0:
            print(
                f"test suite failed: {relative.as_posix()}",
                file=sys.stderr,
                flush=True,
            )
            if github_actions:
                _emit_github_failure(relative, completed.returncode)
            exit_code = exit_code or completed.returncode or 1
    counts = [r["counts"] for r in results]
    totals = None
    if counts and all(isinstance(c, dict) for c in counts):
        totals = {key: sum(c[key] for c in counts) for key in counts[0]}
    if not results:
        exit_code = 2
    summary = {"suites": results, "suite_count": len(results), "totals": totals,
               "unrun_suites": [s.relative_to(root).as_posix() for s in suites[len(results):]],
               "failed_suites": sum(r["exit_code"] != 0 for r in results),
               "exit_code": exit_code}
    if test_patterns is not None:
        summary["unrun_targets"] = [
            {"suite": suite.relative_to(root).as_posix(), "pattern": test_patterns[index]}
            for index, suite in enumerate(suites) if index >= len(results)
        ]
    print("CHAOS_TEST_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return exit_code


def _emit_github_failure(suite: Path, returncode: int) -> None:
    message = _escape_workflow_data(
        f"suite={suite.as_posix()}; exit_code={returncode}"
    )
    print(f"::error title=Chaos Agent test suite failed::{message}", flush=True)


def _escape_workflow_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every Chaos Agent Feature and integration test suite."
    )
    parser.add_argument(
        "--list", action="store_true", help="list discovered suites without running them"
    )
    parser.add_argument("--suite-timeout", type=float, default=600,
                        help="seconds per suite (default: 600); dump stacks then clean up")
    parser.add_argument("--split-root-modules", action="store_true",
                        help="supervise each root integration module separately; same deadline")
    options = parser.parse_args(arguments)
    if not math.isfinite(options.suite_timeout) or options.suite_timeout <= 0:
        parser.error("--suite-timeout must be finite and positive")
    root = repository_root()
    try:
        suites = discover_test_suites(root)
    except RuntimeError as error:
        print(f"test discovery error: {error}", file=sys.stderr)
        return 2
    if options.list:
        for suite in suites:
            print(suite.relative_to(root).as_posix())
        return 0
    return run_test_suites(root, suites, options.suite_timeout,
                           split_root_modules=options.split_root_modules)


if __name__ == "__main__":
    raise SystemExit(main())
