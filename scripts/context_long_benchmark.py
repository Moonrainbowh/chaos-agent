"""Paired real-API continuations from identical synthetic long-history scenes."""
import argparse
import asyncio
import dataclasses
import json
import random
import time
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.config.loader import load_runtime_config
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.rules import RuleLoader
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context_windows.builder import WindowContextBuilder
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.handoff import HandoffWriter
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
from code_agent.context_windows.tools import WindowToolService
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.core.models import Message, ActionResult
from code_agent.evaluation.long_context_cases import long_context_cases, pilot_case, history_documents, _public_input
from code_agent.evaluation.long_context_metrics import usage_metrics, paired_summary
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent_win.context_experiment_host import ExperimentDispatcher, seed_history, verify_source, immutable_manifest
from code_agent_win.managed_context import configured_counter
from code_agent_win.runtime_support import model_client


async def run_case(case, arm, profile, options, documents):
    folder = options.output / case.identifier / arm
    folder.mkdir(parents=True, exist_ok=False)
    workspace = folder / "workspace"
    workspace.mkdir()
    for name, content in documents.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    before = immutable_manifest(workspace)
    hidden = case.oracle()
    namespace = {}
    exec(case.correct_source, namespace)
    rng = random.Random(391)
    public_inputs = [_public_input(case.identifier, rng) for _ in range(12)]
    public = [{"input": x, "expected": namespace["solve"](x)} for x in public_inputs]
    initial = verify_source(workspace, hidden)
    if initial["passed"]:
        raise RuntimeError("fixture must fail before model execution")
    sessions = SQLiteSessionRepository(folder / "sessions.sqlite3")
    thread = await sessions.create_thread()
    dispatcher = ExperimentDispatcher(workspace, public)
    dispatcher.inner.context_actions = WindowToolService(sessions, lambda: thread)
    await seed_history(sessions, thread, dispatcher, case, documents)
    policy = WindowPolicy(strategy=arm, work_tokens=options.window, handoff_tokens=options.handoff,
                          safety_tokens=options.safety, task_tokens=5_000_000)
    client = model_client(profile.provider, reasoning_effort="low", max_output_tokens=profile.max_output_tokens)
    counter = configured_counter(profile.provider.model)
    limits = ApiContextLimits(profile.context_window, profile.max_output_tokens)
    guarded = BudgetedWindowClient(client, sessions, lambda: thread, policy, limits, counter)
    config = ContextConfig(workspace, workspace, "You are repairing a Python data contract. "
        "Use tools to inspect current files and verify changes. Historical summaries are unverified. "
        "Do not edit audit evidence. Respond briefly when done.", repo_map_enabled=False)
    inner = WorkspaceContextBuilder(config, RuleLoader(dispatcher.guard, dispatcher.files, config),
        RepoMapBuilder(dispatcher.files, config), DeterministicCompactor(config))
    context = WindowContextBuilder(inner, sessions, policy, limits, counter,
                                  HandoffWriter(guarded, counter, policy, limits))
    engine = AgentEngine(guarded, context, dispatcher, sessions,
        limits=EngineLimits(24, 80, 12, policy.task_tokens), model_name=profile.provider.model,
        capability_strategy=CapabilityStrategy.LEGACY)
    return await execute_case(case, arm, folder, workspace, before, sessions, thread,
                              dispatcher, engine, client, counter, options)


async def execute_case(case, arm, folder, workspace, before, sessions, thread,
                       dispatcher, engine, client, counter, options):
    initial_tokens = counter.request("", await sessions.load_messages(thread), dispatcher.tools())
    record = {"case": case.identifier, "arm": arm, "seeded_history_tokens": initial_tokens,
              "initial_failed_verification": True, "status": "running"}
    events, started = [], time.monotonic()
    async def consume():
        async for event in engine.run("Continue the repair. A regression remains in solution.py; "
                                     "recheck current implementation against the accepted contract and verify it.", thread_id=thread):
            value = event.to_dict()
            events.append(value)
            (folder / "progress.json").write_text(json.dumps({"events": len(events), "last": event.kind.value}), encoding="utf-8")
    try:
        await asyncio.wait_for(consume(), options.timeout)
        record["status"] = "completed"
    except Exception as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        await client.aclose()
    outcome = await asyncio.to_thread(verify_source, workspace, case.oracle())
    unchanged = before == immutable_manifest(workspace)
    windows = await sessions.context_records(thread, "window")
    usage = usage_metrics(await sessions.context_records(thread, "usage"))
    record.update(seconds=round(time.monotonic()-started, 3),
                  passed=outcome["passed"] and unchanged and record["status"] == "completed" and usage["unknown_requests"] == 0,
                  hidden_verification=outcome, protected_files_unchanged=unchanged, boundary_count=len(windows),
                  reads=dispatcher.reads, duplicate_reads=dispatcher.duplicate_reads,
                  history_reads=dispatcher.history_reads, usage=usage)
    (folder / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (folder / "events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    (folder / "read-trace.json").write_text(json.dumps(dispatcher.trace, indent=2), encoding="utf-8")
    print(json.dumps(record), flush=True)
    return record


async def main():
    options = arguments()
    runtime = load_runtime_config(cli_profile="gpt56_sol")
    profile = next(p for p in runtime.profiles if p.name == "gpt56_sol")
    cases = (pilot_case(),) if options.pilot else long_context_cases()
    counter = configured_counter(profile.provider.model)
    options.output.mkdir(parents=True, exist_ok=True)
    manifest = {"kind": "fixed synthetic long-history, real-API repair continuations",
        "model": profile.provider.model, "api": profile.provider.api.value, "reasoning": "low",
        "window": options.window, "prepare_ratio": .75, "rotate_ratio": .875,
        "output_reserve": profile.max_output_tokens, "safety": options.safety, "handoff": options.handoff,
        "task_token_limit": 5_000_000, "maximum_model_turns":24, "per_run_timeout":options.timeout,
        "case_ids": [c.identifier for c in cases], "arms": ["summary", "boundary"],
        "order": "alternating AB/BA by case index", "sequential":True,
        "recovery_definition": "initial independent verification fails; after at least one committed boundary final hidden checks pass",
        "duplicate_read_definition": "read_file of same path and unchanged content, including pre-boundary seeded reads",
        "baseline": "full-source ordinary summary; same tools, output allowance, model, hard cap and task budget",
        "limits": "single paired repeat; synthetic history; no automatic promotion; hidden oracle never copied into workspace"}
    (options.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    results = []
    for index, case in enumerate(cases):
        documents = prepare_documents(case, counter, options)
        arms = ("summary", "boundary") if index % 2 == 0 else ("boundary", "summary")
        for arm in arms:
            result = await run_case(case, arm, profile, options, documents)
            results.append(result)
            payload = {"results": results, "aggregate": paired_summary(results)}
            (options.output / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--window", type=int, default=256000)
    parser.add_argument("--history-tokens", type=int, default=180000)
    parser.add_argument("--handoff", type=int, default=8192)
    parser.add_argument("--safety", type=int, default=16000)
    parser.add_argument("--timeout", type=int, default=600)
    return parser.parse_args()


def prepare_documents(case, counter, options):
    target = int(options.window * .90)
    raw = options.history_tokens
    for _ in range(5):
        documents = history_documents(case, counter, raw)
        messages = []
        for index, (name, content) in enumerate(documents.items()):
            result = ActionResult(str(index), "read_file", {"path": name, "text": content,
                "total_lines": len(content.splitlines()), "encoding": "utf-8", "bom": False, "newline": "lf"})
            messages.append(Message("tool", json.dumps(result.to_dict(), ensure_ascii=False), tool_call_id=str(index)))
        estimate = counter.request("", messages, ()) + 4000
        if abs(estimate-target) < options.window*.012:
            return documents
        raw = max(1000, int(raw*target/estimate))
    raise RuntimeError("could not calibrate synthetic history before model execution")


if __name__ == "__main__":
    asyncio.run(main())
