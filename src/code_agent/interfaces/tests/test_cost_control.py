from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.core.limits import EngineLimits
from code_agent.core.models import Usage
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.interfaces.cost_control import TaskCostControl, format_cost_report
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.sessions.repository import SQLiteSessionRepository


class TaskCostControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_durable_tokens_and_configured_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract(
                    "verify usage",
                    TaskAuthorization.local_workspace(str(root)),
                    profile_id="priced",
                    model="model-x",
                    protocol="responses",
                    endpoint_host="api.example.test",
                ),
            )
            await sessions.get_or_create_task_budget(
                thread_id, "model-x", EngineLimits(max_total_tokens=1_000_000)
            )
            await sessions.consume_task_usage(task.id, Usage(250_000, 100_000))
            provider = ProviderConfig(
                "https://api.example.test", "model-x", ApiProtocol.RESPONSES, "KEY"
            )
            profile = ModelProfile(
                "priced", provider, 1_000_000, 100_000,
                input_cost_per_million=2.0,
                output_cost_per_million=8.0,
            )
            control = TaskCostControl(sessions, {"priced": profile})

            report = await control.report(task_id=task.id, thread_id=None)

            self.assertEqual(report.total_tokens, 350_000)
            self.assertIn("Estimated total: $1.300000", format_cost_report(report))


if __name__ == "__main__":
    unittest.main()
