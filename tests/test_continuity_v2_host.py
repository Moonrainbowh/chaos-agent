import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from code_agent_win.continuity_run import run_arm
from code_agent_win.continuity_dispatcher import ContinuityDispatcher
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ToolCall


class ContinuityV2HostTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_edits_leave_files_intact_and_emit_separate_policy_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, state = root / "workspace", root / "state"
            workspace.mkdir()
            state.mkdir()
            (workspace / "tests").mkdir()
            (workspace / "batch.py").write_text("original", encoding="utf-8")
            (workspace / "tests/test_resume.py").write_text("protected", encoding="utf-8")
            dispatcher = ContinuityDispatcher(workspace, state)
            dispatcher.process_instance = "test-instance"
            for stage, path in ((1, "batch.py"), (3, "tests/new.py"), (2, "tests/test_resume.py")):
                dispatcher.stage = stage
                request = ToolCall(f"call-{stage}", "write_file", {"path": path, "content": "changed"})
                result = await dispatcher.dispatch(request, CancellationToken())
                self.assertTrue(result.is_error)
            rows = [json.loads(s) for s in (state / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["policy_rejection"] for r in rows],
                             ["diagnosis-read-only", "diagnosis-read-only", "protected-path"])
            self.assertEqual((workspace / "batch.py").read_text(), "original")
            self.assertEqual((workspace / "tests/test_resume.py").read_text(), "protected")
            self.assertFalse((workspace / "tests/new.py").exists())

    async def test_staged_restart_retains_real_pending_failure_and_note_lifecycle(self):
        options = SimpleNamespace(mode="offline", profile="gpt56_luna", model="gpt-5.6-luna",
                                  fixture_version="v2", effort="low", task_tokens=300000, timeout=60)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "D"
            record = await asyncio.wait_for(run_arm(folder, "D", options), 120)
            self.assertTrue(record["passed"], record)
            self.assertTrue(record["challenge_coverage_passed"], record)
            self.assertTrue(record["note_probe_lifecycle_passed"], record)
            self.assertEqual(record["full_chain_status"], "review-required")
            self.assertEqual(record["committed_windows"], 3)
            self.assertEqual(record["model_turns"], 10)
            self.assertEqual(record["dimensions"]["constraint_attempt_count"], 0)
            self.assertEqual(record["dimensions"]["note_semantics"], "review-required")
            self.assertTrue(record["api_usage_is_scripted"])
            self.assertEqual(len({r["process_instance"] for r in record["receipts"]}), 2)
            observations = {r["label"]: r for r in record["observations"]}
            self.assertTrue(observations["before-patch"]["passed"])
            self.assertFalse(observations["after-patch"]["passed"])
            self.assertFalse(observations["after-diagnosis-3"]["passed"])
            self.assertTrue(observations["final"]["passed"])
            self.assertEqual(len(list((folder / "stages").iterdir())), 5)
            rows = [json.loads(s) for s in (folder / "host/actions.jsonl").read_text(encoding="utf-8").splitlines()]
            writes = [r for r in rows if r["request"]["name"] == "write_file"]
            self.assertEqual([r["stage"] for r in writes], [2, 4])
