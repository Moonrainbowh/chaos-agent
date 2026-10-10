"""Conservative diff selection; execution stays in the existing supervised runner."""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Sequence

if __package__:
    from .run_tests import discover_test_suites, repository_root, run_test_suites
else:
    from run_tests import discover_test_suites, repository_root, run_test_suites

# Reviewed leaf Units only; unmapped production Units take the full route.
LOCAL_UNITS = {
    "src/code_agent/evaluation/experience_metrics.py": (),
    "src/code_agent/evaluation/long_context_metrics.py": ("test_continuity_host.py",),
}
ROOT_DOCUMENTS = {"README.md", "AGENTS.md", "AGENTS.python.md", "THIRD_PARTY_NOTICES.md"}


def shared_test_package(root: Path, feature: str) -> bool:
    package = f"code_agent.{feature}.tests"
    own_suite = root / "src" / "code_agent" / feature / "tests"
    for directory in (root / "src" / "code_agent", root / "tests", root / "chaos_agent" / "remote" / "tests"):
        for source in directory.rglob("*.py"):
            if own_suite not in source.parents and package in source.read_text(encoding="utf-8"):
                return True
    return False


def shared_root_module(root: Path, module: str) -> bool:
    target = root / "tests" / f"{module}.py"
    for directory in (root / "src" / "code_agent", root / "tests", root / "chaos_agent"):
        for source in directory.rglob("*.py"):
            if source == target:
                continue
            text = source.read_text(encoding="utf-8")
            if module not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Import) and any(alias.name in {module, f"tests.{module}"} for alias in node.names):
                    return True
                if isinstance(node, ast.ImportFrom) and (
                    node.module in {module, f"tests.{module}"} or
                    (node.module == "tests" and any(alias.name == module for alias in node.names))
                ):
                    return True
            if re.search(r"['\"](?:tests\.)?" + re.escape(module) + r"['\"]", text):
                return True
    return False


def full_plan(reason: str) -> dict:
    return {"mode": "full", "reason": reason, "targets": []}


def select_plan(paths: Sequence[str], root: Path) -> dict:
    targets: set[tuple[str, str | None]] = set()
    production_features = set()
    for value in paths:
        path = PurePosixPath(value)
        parts = path.parts
        if path.is_absolute() or ".." in parts or "\\" in value:
            return full_plan("unrecognized path")
        if value in ROOT_DOCUMENTS or (parts and parts[0] == "docs" and path.suffix in {".md", ".rst", ".txt"}) or (
            len(parts) == 4 and parts[:2] == ("src", "code_agent") and parts[3] == "AGENTS.md"
        ):
            continue
        if len(parts) == 2 and parts[0] == "tests" and re.fullmatch(r"test_[\w]+\.py", parts[1]):
            if not (root / value).is_file():
                return full_plan("deleted root test module")
            if shared_root_module(root, path.stem):
                return full_plan("shared root test module")
            targets.add(("tests", parts[1]))
            continue
        if len(parts) >= 4 and parts[:2] == ("src", "code_agent"):
            feature = parts[2]
            suite = f"src/code_agent/{feature}/tests"
            if not (root / suite).is_dir():
                return full_plan("missing Feature test suite")
            if parts[3] == "tests":
                if not path.name.startswith("test_") or path.suffix != ".py" or shared_test_package(root, feature):
                    return full_plan("shared or unknown test helper")
                targets.add((suite, None))
                continue
            if value not in LOCAL_UNITS:
                return full_plan("shared or unmapped production Feature")
            production_features.add(feature)
            targets.add((suite, None))
            for module in LOCAL_UNITS[value]:
                if not (root / "tests" / module).is_file():
                    return full_plan("missing mapped integration module")
                targets.add(("tests", module))
            continue
        return full_plan("entrypoint, dependency, packaging or unknown change")
    if len(production_features) > 1:
        return full_plan("multiple production Features")
    if not targets:
        return {"mode": "docs", "reason": "documentation only or empty verified diff", "targets": []}
    return {"mode": "selected", "reason": "affected test suites and mapped integration modules",
            "targets": [{"suite": suite, "pattern": pattern}
                        for suite, pattern in sorted(targets, key=lambda item: (item[0], item[1] or ""))]}


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    if not base or not head or set(base) == {"0"}:
        raise ValueError("missing baseline or new branch")
    commits = []
    for ref in (base, head):
        resolved = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
                                  cwd=root, check=True, capture_output=True)
        commits.append(resolved.stdout.decode("ascii").strip())
    checkout = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root,
                              check=True, capture_output=True).stdout.decode("ascii").strip()
    if commits[1] != checkout:
        raise ValueError("requested head differs from checkout")
    result = subprocess.run(["git", "diff", "--name-status", "-z", "--find-renames", *commits, "--"],
                            cwd=root, check=True, capture_output=True)
    fields = result.stdout.decode("utf-8").split("\0")
    paths = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        count = 2 if status.startswith(("R", "C")) else 1
        if not re.fullmatch(r"[AMDTRCUXB][0-9]*", status) or index + count >= len(fields):
            raise ValueError("invalid git diff record")
        paths.extend(fields[index + 1:index + 1 + count])
        index += count + 1
    return paths


def event_refs(event: dict, event_name: str) -> tuple[str, str]:
    if event_name == "pull_request":
        return event["pull_request"]["base"]["sha"], "HEAD"
    if event_name == "push":
        if event.get("deleted"):
            raise ValueError("deleted branch")
        return event["before"], event["after"]
    raise ValueError("manual run uses full daily scope")


def plan_from_git(root: Path, base: str | None, head: str | None) -> dict:
    try:
        if base is None or head is None:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            base, head = event_refs(event, os.environ["GITHUB_EVENT_NAME"])
        paths = changed_paths(root, base, head)
        status = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=normal"],
                                cwd=root, check=True, capture_output=True)
        if status.stdout:
            return {**full_plan("worktree has uncommitted changes"), "base": base, "head": head,
                    "changed_paths": paths}
        plan = select_plan(paths, root)
        plan.update({"base": base, "head": head, "changed_paths": paths})
        return plan
    except (OSError, ValueError, KeyError, SyntaxError, subprocess.CalledProcessError) as error:
        return full_plan(f"baseline unavailable: {type(error).__name__}")


def execute_plan(root: Path, plan: dict, timeout: float) -> int:
    if plan["mode"] == "docs":
        print("CHAOS_CI_RESULT documentation only; product tests not required", flush=True)
        return 0
    if plan["mode"] == "full":
        return run_test_suites(root, discover_test_suites(root), timeout, split_root_modules=os.name == "nt")
    targets = plan["targets"]
    return run_test_suites(root, [root / target["suite"] for target in targets], timeout,
                           test_patterns=[target["pattern"] for target in targets])


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--plan", "--dry-run", action="store_true", dest="plan_only")
    parser.add_argument("--suite-timeout", type=float, default=600)
    options = parser.parse_args(arguments)
    if (options.base is None) != (options.head is None):
        parser.error("--base and --head must be supplied together")
    if not math.isfinite(options.suite_timeout) or options.suite_timeout <= 0:
        parser.error("--suite-timeout must be finite and positive")
    root = repository_root()
    plan = plan_from_git(root, options.base, options.head)
    print("CHAOS_CI_PLAN " + json.dumps(plan, sort_keys=True), flush=True)
    if options.plan_only:
        return 0
    return execute_plan(root, plan, options.suite_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
