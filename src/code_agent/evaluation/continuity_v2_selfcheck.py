"""Positive and negative controls for challenge coverage, independent of paid models."""
import tempfile
from dataclasses import replace
from pathlib import Path

from .continuity_selfcheck import OfflineAdapter
from .continuity_v2_driver import drive
from .continuity_v2_fixture import GOLDEN_AFTER, GOLDEN_BEFORE, VERSION, scenario
from .grader import DeterministicGrader
from .models import ScenarioResult
from .runner import ScenarioRunner


class StagedOfflineAdapter(OfflineAdapter):
    async def set_stage(self, stage):
        self.stage = stage

    async def work(self, workspace, message, rounds):
        if self.stage in (1, 3):
            return self.receipt
        self.source = GOLDEN_BEFORE if self.stage == 2 else GOLDEN_AFTER
        if self.mutation == "never-migrate":
            self.source = GOLDEN_BEFORE
        if self.mutation == "already-compatible":
            self.source = GOLDEN_BEFORE.replace(
                "    for line, identity, raw in records(source):",
                "    for item in records(source):\n"
                "        line, identity, raw = (item.line, item.identity, item.amount) "
                "if hasattr(item, 'line') else item")
        result = await super().work(workspace, message, rounds)
        if self.stage >= 4 and self.mutation == "stale-verification":
            result = replace(result, verified_digest=None)
        return result


async def check_one(root, arm, mutation=None):
    case, journal = scenario(root, arm), {}
    async def execute(workspace, prompt, recorder):
        adapter = StagedOfflineAdapter(GOLDEN_BEFORE, case.expected.verifiers[0])
        adapter.mutation = mutation
        journal.update(await drive(workspace, arm, adapter))
        recorder.record_status("completed")
        recorder.record_budget(model_turns=0, tool_calls=0)
        recorder.seal()
        return ScenarioResult("completed")
    result, observation = await ScenarioRunner().run(case, execute)
    grade = DeterministicGrader().grade(case, result, observation)
    passed = grade.passed and journal.get("fresh_agent_verification", False)
    return {"variant": arm, "mutation": mutation, "final_passed": passed,
            "coverage_passed": passed and journal.get("coverage", {}).get("status") == "covered",
            "grade_failures": list(grade.failures), **journal}


async def selfcheck():
    with tempfile.TemporaryDirectory(prefix="continuity-v2-source-") as directory:
        root = Path(directory)
        positives = [await check_one(root, arm) for arm in "ABCD"]
        negatives = [await check_one(root, "D", mutation) for mutation in
                     ("never-migrate", "already-compatible", "stale-verification")]
    return {"fixture_version": VERSION, "mode": "offline-scripted-reference",
            "real_api_status": "NOT_RUN", "reference": positives, "negative_controls": negatives,
            "selfcheck_passed": all(r["coverage_passed"] for r in positives)
            and all(not r["coverage_passed"] for r in negatives)
            and negatives[1]["final_passed"]}
