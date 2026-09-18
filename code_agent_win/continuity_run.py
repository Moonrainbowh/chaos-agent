"""Combine the frozen case, process driver and independent graders."""
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from code_agent.evaluation.continuity_driver import drive
from code_agent.evaluation.continuity_oracle import scenario
from code_agent.evaluation.continuity_plan import manifest
from code_agent.evaluation.grader import DeterministicGrader
from code_agent.evaluation.long_context_metrics import usage_metrics
from code_agent.evaluation.models import ScenarioResult
from code_agent.evaluation.runner import ScenarioRunner
from code_agent.core.limits import EngineLimits
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent_win.continuity_process import ProcessContinuityAdapter


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_arm(folder: Path, arm: str, options):
    folder.mkdir(parents=True, exist_ok=False)
    fixture_root = folder / "fixture-root"
    fixture_root.mkdir()
    version = getattr(options, "fixture_version", "v1")
    case_builder, plan_builder, driver = scenario, manifest, drive
    if version == "v2":
        from code_agent.evaluation.continuity_v2_fixture import scenario as case_builder
        from code_agent.evaluation.continuity_v2_plan import manifest as plan_builder
        from code_agent.evaluation.continuity_v2_driver import drive as driver
    elif version != "v1":
        raise ValueError("unknown fixture version")
    case = case_builder(fixture_root, arm)
    _write(folder / "plan.json", plan_builder(arm))
    journal, stats, receipts = {}, {}, []
    started = time.monotonic()

    async def execute(workspace, prompt, recorder):
        adapter = ProcessContinuityAdapter(workspace, folder / "host", options)
        try:
            arguments = {"artifact_dir": folder / "stages"} if version == "v2" else {}
            journal.update(await driver(workspace, arm, adapter, **arguments))
            stats.update(await adapter.stats())
            recorder.record_status("completed")
            recorder.record_budget(model_turns=stats["model_turns"], tool_calls=stats["tool_calls"])
            recorder.seal()
            return ScenarioResult("completed")
        finally:
            receipts.extend(adapter.receipts)
            await adapter.close()
            stats.update(await durable_stats(folder / "host", options))
            shutil.copytree(workspace, folder / "final-workspace")

    execute.isolation_mode = "subprocess-tree"
    result, observation = await ScenarioRunner().run(case, execute)
    grade = DeterministicGrader().grade(case, result, observation)
    record = _record(arm, options, stats, journal, receipts, grade, observation, started)
    record["memory_tools"] = memory_metrics(folder / "host")
    record["fixture_version"] = case.fixture_version
    if version == "v2":
        _v2_dimensions(record, journal, folder, arm)
    _write(folder / "result.json", record)
    return record


def _v2_dimensions(record, journal, folder, arm):
    from code_agent.evaluation.continuity_v2_metrics import read_metrics
    dimensions = read_metrics(folder / "host/actions.jsonl", arm in "CD")
    record.update(dimensions=dimensions, coverage=journal.get("coverage", {}),
                  observations=journal.get("observations", []))
    record["challenge_coverage_passed"] = (record["passed"] and
        record["coverage"].get("status") == "covered" and not dimensions["constraint_attempt_count"])
    record["note_probe_lifecycle_passed"] = (dimensions["note_lifecycle"] == "observed") if arm in "CD" else None
    record["full_chain_status"] = "review-required" if (record["challenge_coverage_passed"] and
        (arm not in "CD" or record["note_probe_lifecycle_passed"])) else "not-covered"


def _record(arm, options, stats, journal, receipts, grade, observation, started):
    # The first persistent context is committed while the control arm starts.
    # A has no host-scheduled switches, whereas B/C/D each add three.
    expected = 1 if arm == "A" else 3
    count = len(stats.get("windows", []))
    fresh = journal.get("fresh_agent_verification", False)
    usage = usage_metrics(stats.get("usage", []))
    failures = list(grade.failures)
    if not fresh:
        failures.append("missing successful agent verification for final workspace")
    if count != expected:
        failures.append("unexpected committed window count")
    if usage["unknown_requests"]:
        failures.append("unknown API usage")
    return {"variant": arm, "mode": options.mode, "passed": not failures,
            "model": options.model, "reasoning_effort": options.effort,
            "task_token_limit": options.task_tokens, "failures": failures,
            "seconds": round(time.monotonic() - started, 3), "committed_windows": count,
            "fresh_agent_verification": fresh, "model_turns": stats.get("model_turns"),
            "tool_calls": stats.get("tool_calls"), "usage": usage,
            "api_usage_is_scripted": options.mode == "offline",
            "verifiers": [asdict(item) for item in observation.verifier_results],
            "events": journal.get("events", []), "receipts": receipts,
            "cleanup": observation.workspace_removed,
            "scope": "production engine/context/tools/sqlite; thread continuation, not TUI TaskRecord"}


async def durable_stats(state, options):
    """Recover real usage even when the worker exits on a provider error."""
    identity = state / "identity.json"
    if not identity.exists():
        return {}
    thread = json.loads(identity.read_text(encoding="utf-8"))["thread"]
    sessions = SQLiteSessionRepository(state / "sessions.sqlite3")
    budget = await sessions.get_or_create_task_budget(thread, options.model, EngineLimits(24, 100, 12, options.task_tokens))
    return {"model_turns": budget.model_turns, "tool_calls": budget.tool_calls,
            "windows": list(await sessions.context_records(thread, "window")),
            "usage": list(await sessions.context_records(thread, "usage"))}


def memory_metrics(state):
    result = {"history_calls": 0, "notes_calls": 0, "successful_history_calls": 0,
              "successful_notes_calls": 0, "by_tool": {}}
    path = state / "actions.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else ():
        entry = json.loads(line)
        name = entry["request"]["name"]
        family = "history" if name.startswith("history_") else "notes" if name.startswith("notes_") else None
        if family:
            result[family + "_calls"] += 1
            result["successful_" + family + "_calls"] += int(not entry["result"]["is_error"])
            result["by_tool"][name] = result["by_tool"].get(name, 0) + 1
    return result
