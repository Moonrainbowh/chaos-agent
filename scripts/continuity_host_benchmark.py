"""Run the continuity case through real worker processes and production engine."""
import argparse
import asyncio
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from code_agent_win.continuity_run import run_arm
from code_agent_win.continuity_runtime import selected_profile
from code_agent_win.continuity_process import verify_freeze


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "api"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", default="ABCD")
    parser.add_argument("--fixture-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--profile", default="gpt56_luna")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--task-tokens", type=int, default=300000)
    parser.add_argument("--timeout", type=int, default=600)
    result = parser.parse_args()
    if not result.arms or len(set(result.arms)) != len(result.arms) or set(result.arms) - set("ABCD"):
        parser.error("arms must be unique letters from ABCD")
    if result.task_tokens < 20000 or not 1 <= result.timeout <= 900:
        parser.error("invalid task token or timeout bound")
    result.output = result.output.resolve()
    return result


async def main(options):
    metadata = {"fixture": "csv-continuity-" + options.fixture_version, "mode": options.mode, "arms": options.arms,
                "model": options.model, "effort": options.effort, "profile": options.profile,
                "task_tokens": options.task_tokens, "output_tokens": 8192, "work_tokens": 64000,
                "safety_tokens": 2000, "context_strategy": "persistent", "repeats": 1,
                "purpose": "pilot; no promotion or general performance claim"}
    sources = [*ROOT.glob("code_agent_win/continuity_*.py"),
               *ROOT.glob("src/code_agent/evaluation/continuity_*.py"), Path(__file__)]
    metadata["source_versions"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(set(sources))}
    if options.mode == "api":
        verify_freeze(ROOT, required=True)
        profile = selected_profile(options)
        metadata.update(model=profile.provider.model, api=profile.provider.api.value,
                        configured_combined_capacity=profile.context_window)
    options.output.mkdir(parents=True, exist_ok=False)
    (options.output / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    results = []
    for arm in options.arms:
        print(f"Starting {options.mode} arm {arm}", flush=True)
        record = await run_arm(options.output / arm, arm, options)
        results.append(record)
        (options.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        fields = ["variant", "passed", "failures", "model_turns", "tool_calls"]
        if options.fixture_version == "v2":
            fields += ["challenge_coverage_passed", "note_probe_lifecycle_passed", "full_chain_status"]
        print(json.dumps({k: record[k] for k in fields}), flush=True)
    return 0 if all(accepted(r) for r in results) else 1


def accepted(record):
    """A correct final implementation cannot mask uncovered v2 challenges in CLI status."""
    if record.get("fixture_version") == "csv-continuity-v2":
        return (record["passed"] and record["challenge_coverage_passed"]
                and record["note_probe_lifecycle_passed"] is not False)
    return record["passed"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main(arguments())))
