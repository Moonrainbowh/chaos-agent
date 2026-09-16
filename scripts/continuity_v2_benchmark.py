"""Export v2 public fixtures or run isolated offline challenge controls."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from code_agent.evaluation.continuity_v2_fixture import READER_V2, fixture_files
from code_agent.evaluation.continuity_v2_plan import manifest
from code_agent.evaluation.continuity_v2_selfcheck import selfcheck


def export(destination):
    destination.mkdir(parents=True, exist_ok=False)
    for arm in "ABCD":
        for relative, source in fixture_files().items():
            path = destination / arm / "workspace" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        controller = destination / arm / "controller"
        controller.mkdir()
        (controller / "plan.json").write_text(json.dumps(manifest(arm), indent=2), encoding="utf-8")
        (controller / "reader-v2.py").write_text(READER_V2, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.export and not args.selfcheck:
        parser.error("choose --export and/or --selfcheck")
    if args.report and (not args.selfcheck or args.report.exists()):
        parser.error("report requires selfcheck and a new path")
    if args.export:
        export(args.export)
    if args.selfcheck:
        report = asyncio.run(selfcheck())
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as target:
                json.dump(report, target, indent=2)
        print(json.dumps({k: report[k] for k in ("fixture_version", "mode", "selfcheck_passed")}))
        return 0 if report["selfcheck_passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
