"""Explicit offline entry points for the independently installed add-on."""
import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("selfcheck", help="offline reference and negative controls")
    check.add_argument("--fixture-version", choices=("v1", "v2"), default="v1")
    host = commands.add_parser("host-offline", help="fixed model through real Host worker processes")
    host.add_argument("--fixture-version", choices=("v1", "v2"), default="v1")
    host.add_argument("--arm", choices=tuple("ABCD"), default="A")
    host.add_argument("--output", type=Path, required=True, help="new owned output directory")
    options = parser.parse_args()
    if options.command == "selfcheck":
        if options.fixture_version == "v1":
            from code_agent.evaluation.continuity_selfcheck import selfcheck
        else:
            from code_agent.evaluation.continuity_v2_selfcheck import selfcheck
        report = asyncio.run(selfcheck())
        accepted = report["selfcheck_passed"]
    else:
        from chaos_agent.continuity_run import run_arm
        target = options.output.resolve()
        if target.exists():
            parser.error("output already exists; choose a new owned directory")
        target.mkdir(parents=True)
        settings = SimpleNamespace(mode="offline", fixture_version=options.fixture_version,
                                   profile="offline", model="offline-fixed", effort="high",
                                   task_tokens=300000, timeout=600)
        report = asyncio.run(run_arm(target / options.arm, options.arm, settings))
        accepted = report["passed"]
        if options.fixture_version == "v2":
            accepted = (accepted and report["challenge_coverage_passed"]
                        and report["note_probe_lifecycle_passed"] is not False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if accepted else 1
