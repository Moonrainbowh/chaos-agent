import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from code_agent_win.continuity_run import run_arm, durable_stats
from code_agent.core.limits import EngineLimits
from code_agent.evaluation.long_context_metrics import usage_metrics
from code_agent.sessions.repository import SQLiteSessionRepository


class ContinuityHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_usage_survives_worker_loss(self):
        options = SimpleNamespace(model="gpt-5.6-luna", task_tokens=300000)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            sessions = SQLiteSessionRepository(state / "sessions.sqlite3")
            thread = await sessions.create_thread()
            (state / "identity.json").write_text(json.dumps({"thread": thread}), encoding="utf-8")
            await sessions.get_or_create_task_budget(thread, options.model, EngineLimits(20, 100, 12, 300000))
            await sessions.reserve_task_budget(thread, model_turns=1)
            await sessions.reserve_context_call(thread, "interrupted-call", 1000, 300000, "main")
            stats = await durable_stats(state, options)
            usage = usage_metrics(stats["usage"])
            self.assertEqual(stats["model_turns"], 1)
            self.assertEqual(usage["unknown_requests"], 1)
            self.assertEqual(usage["reserved_unknown_tokens"], 1000)

    async def test_real_worker_restart_restores_history_notes_and_final_verification(self):
        options = SimpleNamespace(mode="offline", profile="gpt56_luna", model="gpt-5.6-luna",
                                  effort="low", task_tokens=300000, timeout=60)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "D"
            record = await asyncio.wait_for(run_arm(folder, "D", options), 120)
            self.assertTrue(record["passed"], record)
            self.assertEqual(record["committed_windows"], 3)
            self.assertTrue(record["api_usage_is_scripted"])
            self.assertEqual(record["model_turns"], 8)
            pauses = [r for r in record["receipts"] if r["action"] == "pause"]
            self.assertEqual(pauses[0]["observed_exit_code"], 0)
            identities = {r["process_instance"] for r in record["receipts"]}
            self.assertEqual(len(identities), 2)
            self.assertEqual(len({r["task_id"] for r in record["receipts"]}), 1)
            rows = [json.loads(line) for line in (folder / "host/actions.jsonl").read_text(encoding="utf-8").splitlines()]
            previous = pauses[0]["process_instance"]
            reads = [r for r in rows if r["request"]["name"] == "notes_read_file"
                     and r["process_instance"] != previous]
            self.assertTrue(reads)
            self.assertFalse(reads[0]["result"]["is_error"])
            self.assertGreater(record["memory_tools"]["successful_history_calls"], 0)
            self.assertTrue(record["fresh_agent_verification"])
            self.assertTrue(record["cleanup"])
