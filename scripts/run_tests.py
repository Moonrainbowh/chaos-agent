from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


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

    integration = root / "tests"
    if not integration.is_dir() or not any(integration.glob(TEST_PATTERN)):
        missing.append(integration.relative_to(root))
    else:
        suites.append(integration)

    if missing:
        rendered = ", ".join(path.as_posix() for path in missing)
        raise RuntimeError(f"test suite is missing or empty for: {rendered}")
    return tuple(suites)


def run_test_suites(root: Path, suites: Sequence[Path]) -> int:
    github_actions = os.environ.get("GITHUB_ACTIONS", "").casefold() == "true"
    for suite in suites:
        relative = suite.relative_to(root)
        print(f"=== {relative.as_posix()} ===", flush=True)
        if github_actions:
            command = (
                sys.executable,
                "-m",
                "scripts.run_test_suite",
                "--start-dir",
                str(relative),
                "--pattern",
                TEST_PATTERN,
            )
        else:
            command = (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(relative),
                "-p",
                TEST_PATTERN,
            )
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
        )
        if completed.returncode != 0:
            print(
                f"test suite failed: {relative.as_posix()}",
                file=sys.stderr,
                flush=True,
            )
            if github_actions:
                _emit_github_failure(relative, completed.returncode)
            return completed.returncode or 1
    return 0


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
    options = parser.parse_args(arguments)
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
    return run_test_suites(root, suites)


if __name__ == "__main__":
    raise SystemExit(main())
