"""Offline reference/mutation checks. Never a real-agent benchmark result."""
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from .continuity_driver import Receipt, drive
from .continuity_fixture import FAULTY, GOLDEN, READER_V1, VERSION
from .continuity_oracle import scenario
from .continuity_plan import manifest
from .grader import DeterministicGrader
from .models import ScenarioResult
from .observation import manifest_digest, workspace_manifest
from .runner import ScenarioRunner
from .verifier import SubprocessVerifier


class OfflineAdapter:
    """Scripted host double, including synthetic window/process identities."""

    def __init__(self, source: str, oracle, *, stale=False, revert=False):
        self.source, self.oracle = source, oracle
        self.stale, self.revert = stale, revert
        self.receipt = Receipt("offline-task", "offline-process-1", "window-0", "offline-store")
        self.round = 0
        self.window = 0

    async def work(self, workspace, message, rounds):
        self.round += 1
        (workspace / "batch.py").write_text(self.source, encoding="utf-8")
        if self.revert and self.round >= 3:
            (workspace / "reader.py").write_text(READER_V1, encoding="utf-8")
        if not self.stale or self.round <= 2:
            outcome = await SubprocessVerifier()(workspace, self.oracle, "offline-public")
            digest = manifest_digest(workspace_manifest(workspace)) if outcome.exit_code == 0 else None
            self.receipt = replace(self.receipt, verified_digest=digest)
        return self.receipt

    async def switch(self):
        self.window += 1
        self.receipt = replace(self.receipt, window_id=f"window-{self.window}", verified_digest=None)
        return self.receipt

    async def pause(self):
        self.receipt = replace(self.receipt, process_exited=True, verified_digest=None)
        return self.receipt

    async def resume(self):
        self.receipt = replace(self.receipt, process_exited=False,
                               process_instance="offline-process-2", verified_digest=None)
        return self.receipt


async def check_one(root: Path, variant: str, source: str, *, stale=False, revert=False) -> dict:
    case = scenario(root, variant)
    journal = {}

    async def execute(workspace, prompt, recorder):
        adapter = OfflineAdapter(source, case.expected.verifiers[0], stale=stale, revert=revert)
        journal.update(await drive(workspace, variant, adapter))
        recorder.record_status("completed")
        recorder.record_budget(model_turns=0, tool_calls=0)
        recorder.seal()
        return ScenarioResult("completed")

    result, observation = await ScenarioRunner().run(case, execute)
    grade = DeterministicGrader().grade(case, result, observation)
    fresh = journal.get("fresh_agent_verification", False)
    failures = list(grade.failures)
    if not fresh:
        failures.append("missing successful agent verification for final workspace")
    return {"variant": variant, "passed": grade.passed and fresh,
            "behavior_and_workspace_passed": grade.passed,
            "fresh_scripted_verification": fresh, "failures": failures,
            "verifiers": [asdict(item) for item in observation.verifier_results],
            "events": journal.get("events", []), "cleanup": observation.workspace_removed,
            "mode": "offline-scripted-reference", "real_api_status": "NOT_RUN",
            "model": None, "reasoning_effort": None, "api_tokens": None,
            "history_calls": None, "notes_calls": None}


async def selfcheck() -> dict:
    """Reference must pass all arms; known bad implementations must be rejected."""
    ordinal = GOLDEN.replace("for line, identity, raw in records(source):",
                             "for line, (_, identity, raw) in enumerate(records(source), start=2):")
    rewrite = GOLDEN.replace("    last = load_checkpoint(checkpoint)",
                             '    open(output, "w").close()\n    last = 1')
    with tempfile.TemporaryDirectory(prefix="csv-continuity-source-") as directory:
        root = Path(directory)
        positives = [await check_one(root, arm, GOLDEN) for arm in "ABCD"]
        negative_specs = [("unrepaired", "A", FAULTY, {}),
                          ("ordinal-stale-assumption", "C", ordinal, {}),
                          ("rewrite-output", "A", rewrite, {}),
                          ("revert-external-change", "C", GOLDEN, {"revert": True}),
                          ("stale-validation", "C", GOLDEN, {"stale": True})]
        negatives = []
        for name, arm, source, options in negative_specs:
            record = await check_one(root, arm, source, **options)
            negatives.append({"mutation": name, **record})
        ordinal_control = await check_one(root, "B", ordinal)
    passed = (all(item["passed"] for item in positives)
              and all(not item["passed"] and item["cleanup"] for item in negatives)
              and ordinal_control["passed"])
    return {"fixture_version": VERSION, "selfcheck_passed": passed,
            "mode": "offline-scripted-reference", "real_api_status": "NOT_RUN",
            "plans": [manifest(arm) for arm in "ABCD"], "reference": positives,
            "negative_controls": negatives, "ordinal_before_evolution": ordinal_control}
