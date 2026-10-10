from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.limits import BudgetLeaseTier, EngineLimits, TaskProgressSnapshot
from code_agent.core.models import Usage
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.interfaces.cost_control import TaskCostControl, format_cost_report
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.sessions.repository import SQLiteSessionRepository


class TaskCostControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_task_models_are_unpriced_without_cross_task_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread = await sessions.create_thread()
            task = await sessions.create_task(thread, TaskContract(
                "review", TaskAuthorization.local_workspace(str(root)),
                profile_id="priced", model="model-x", protocol="responses",
                endpoint_host="api.example.test",
            ))
            await sessions.get_or_create_task_budget(thread, "model-x", EngineLimits())
            await sessions.consume_task_usage(task.id, Usage(100, 20))
            profile = ModelProfile("priced", ProviderConfig(
                "https://api.example.test", "model-x", ApiProtocol.RESPONSES, "KEY"
            ), 100_000, 10_000, input_cost_per_million=2, output_cost_per_million=8)
            control = TaskCostControl(sessions, {"priced": profile})

            async def record_request(target: str, model: str) -> None:
                await sessions.append_event(target, AgentEvent(
                    EventKind.MODEL_STARTED, {"model": model}
                ))
                await sessions.append_event(target, AgentEvent(EventKind.MODEL_EVENT, {
                    "event": {"kind": "usage", "usage": Usage(100, 20).to_dict()}
                }))

            await record_request(thread, "model-x")
            sibling = await sessions.create_thread()
            await sessions.create_task(sibling, TaskContract(
                "other task", TaskAuthorization.local_workspace(str(root))
            ))
            await record_request(sibling, "other-task-model")
            await sessions._database.write(lambda connection: connection.executemany(
                "INSERT OR REPLACE INTO conversation_heads "
                "(thread_id,conversation_id,node_id) VALUES (?,?,NULL)",
                [(thread, thread), (sibling, thread)],
            ))
            single = await control.report(task_id=task.id, thread_id=None)
            self.assertIsNotNone(single.total_cost)
            self.assertEqual(single.model, "model-x")
            self.assertIn("other-task-model", single.session_usage.models)

            await record_request(thread, "review-model")
            await sessions.consume_task_usage(task.id, Usage(100, 20))
            mixed = await control.report(task_id=task.id, thread_id=None)
            self.assertEqual((mixed.input_tokens, mixed.output_tokens), (200, 40))
            self.assertEqual(mixed.model, "model-x, review-model")
            self.assertIsNone(mixed.input_cost)
            self.assertIsNone(mixed.output_cost)
            self.assertIsNone(mixed.total_cost)
            rendered = format_cost_report(mixed)
            self.assertIn("Model: model-x, review-model", rendered)
            self.assertIn("Cost: unavailable", rendered)

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
                thread_id,
                "model-x",
                EngineLimits(max_total_tokens=1_000_000),
                BudgetLeaseTier.QUICK,
            )
            await sessions.reserve_task_budget(
                thread_id,
                model_turns=4,
                tool_calls=2,
                progress=TaskProgressSnapshot(reason="initial"),
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
            rendered = format_cost_report(report)
            self.assertIn("Estimated total: $1.300000", rendered)
            self.assertIn("Soft lease (quick): model turns 4/4, tool calls 2/8", rendered)
            self.assertIn("Hard limit: model turns 4/50, tool calls 2/128", rendered)

    async def test_reports_lease_when_pricing_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract(
                    "inspect",
                    TaskAuthorization.local_workspace(str(root)),
                ),
            )
            await sessions.get_or_create_task_budget(
                thread_id,
                "model-x",
                EngineLimits(),
                BudgetLeaseTier.STANDARD,
            )

            report = await TaskCostControl(sessions, {}).report(
                task_id=task.id, thread_id=None
            )
            rendered = format_cost_report(report)

            self.assertIn("Soft lease (standard)", rendered)
            self.assertIn("Cost: unavailable", rendered)


if __name__ == "__main__":
    unittest.main()
