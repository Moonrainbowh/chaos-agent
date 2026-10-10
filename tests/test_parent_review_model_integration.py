"""Configured Host review routing through real providers and public task start."""
import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from chaos_agent.app import create_application
from chaos_agent.parent_review_model import ReviewModelFactory, RuntimeClients
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import ApiContextLimits, RequestBudgetConstraints, WindowPolicy
from code_agent.core.errors import ModelStreamError
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.models import AgentContribution, PluginContributions, PluginManifest
from code_agent.plugins.registry import PluginRegistryBuilder
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from tests.test_s16_source_completion import answer, call, review_answers


CHILD_MARKER = "CONFIGURED_ROUTE_CHILD_18472"
CONFIG = """
[default]
provider = "main"
[providers.main]
api = "responses"
base_url = "https://main.example.test"
model = "main-model"
api_key_env = "MAIN_KEY"
context_window = 128000
max_output_tokens = 4096
[providers.review]
api = "responses"
base_url = "https://review.example.test"
model = "review-model"
api_key_env = "REVIEW_KEY"
context_window = 128000
max_output_tokens = 4096
[agent]
approval_mode = "full-local"
parent_review_profile = "review"
"""


class ConfiguredParentReviewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        (self.root / "names.py").write_text("names = ['Ada']\n", encoding="utf-8")
        config = Path(self.temp.name) / "local" / "chaos-agent" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(CONFIG, encoding="utf-8")
        self.streams, self.bodies = defaultdict(list), defaultdict(list)
        self.first_review_snapshot = None
        self.clients = []
        original = httpx.AsyncClient
        owner = self

        class OfflineHttpClient(original):
            def __init__(self, **options):
                super().__init__(**options, transport=httpx.MockTransport(owner.respond))
                self.route = None
                self.closed_event = asyncio.Event()
                owner.clients.append(self)

            async def send(self, request, **options):
                body = json.loads(request.content)
                self.route = owner.route(body)
                return await super().send(request, **options)

            async def aclose(self):
                await super().aclose()
                self.closed_event.set()

        environment = {
            "USERPROFILE": str(Path(self.temp.name) / "profile"),
            "LOCALAPPDATA": str(Path(self.temp.name) / "local"),
            "PATH": os.environ.get("PATH", ""),
            "CHAOS_CONFIG": str(config), "MAIN_KEY": "offline-main",
            "REVIEW_KEY": "offline-review", "CHAOS_WORKSPACE_MODE": "direct",
        }
        self.env_patch = patch.dict(os.environ, environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.http_patch = patch("httpx.AsyncClient", OfflineHttpClient)
        self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.app = create_application(self.root)
        self.addAsyncCleanup(self.app.aclose)

    @staticmethod
    def route(body):
        if body["model"] == "review-model":
            return "review"
        return "child" if CHILD_MARKER in body["instructions"] else "main"

    async def respond(self, request):
        body = json.loads(request.content)
        route = self.route(body)
        self.bodies[route].append(body)
        if route == "review" and self.first_review_snapshot is None:
            records = await self.app.sessions.context_records(self.last_task.thread_id, "parent_review")
            self.first_review_snapshot = records[0]
        if not self.streams[route]:
            raise AssertionError(f"unexpected {route} request: {body['input'][-2:]}")
        stream = self.streams[route].pop(0)
        if isinstance(stream, Exception):
            raise stream
        events = stream + [{"type": "response.completed", "response": {
            "usage": {"input_tokens": 50, "output_tokens": 5}}}]
        return httpx.Response(200, content="".join(
            "data: " + json.dumps(event) + "\n\n" for event in events))

    async def start(self, prompt):
        task = await self.app.tasks.start(prompt)
        self.last_task = task
        events = [event async for event in self.app.tasks.events(task.id)]
        return await self.app.sessions.load_task(task.id), events

    async def review_run(self, fail=False):
        manifest = PluginManifest("route-test", "route", "1.0.0", "1",
            hashlib.sha256(b"route-test").hexdigest(), "offline", True, True,
            PluginContributions(agents=(AgentContribution("source", AgentMode.MEDIUM,
                CHILD_MARKER, ("read_file", "search_text"), False),)))
        snapshot = PluginRegistryBuilder(self.app.subagents._modes).build((manifest,))
        self.assertEqual(snapshot.errors, ())
        self.app.plugins.stage(snapshot)
        self.assertTrue(self.app.plugins.apply(task_active=False))
        await self.app.runtime_selection.use(topology="team", idle=True)
        self.streams["main"] = [
            [call("disclose", "load_tool_contract", {"name": "delegate_agent"})],
            [call("delegate", "delegate_agent", {"agent_id": "route.source",
                "objective": "Analyze names.py sources.", "required_sources": ["names.py"],
                "token_budget": 60000})]]
        advisory = "Static child advisory."
        self.streams["child"] = [
            [call("read", "read", {"operation": "file", "path": "names.py"})],
            [answer(advisory)]]
        self.streams["review"] = ([httpx.ReadError("offline stream failure")]
            if fail else review_answers(self.root, "names.py", advisory))
        return await self.start("Read only: deep source investigation. Delegate once and review "
            "implementation, contract compliance, test discrimination and unknowns.")

    async def test_plain_parent_does_not_allocate_review_provider(self):
        self.assertEqual(len(self.clients), 1)
        self.streams["main"] = [[answer("A plain answer.")]]
        task, _ = await self.start("Explain briefly.")
        self.assertEqual(task.status.value, "completed")
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(self.bodies["main"][0]["model"], "main-model")
        self.assertFalse(self.bodies["review"])
        await self.app.aclose()
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_configured_review_routes_only_parent_review_and_accounts_once(self):
        task, events = await self.review_run()
        self.assertEqual(task.status.value, "completed", [e.to_dict() for e in events])
        self.assertEqual({key: len(self.bodies[key]) for key in ("main", "child", "review")},
                         {"main": 2, "child": 2, "review": 2})
        for route in ("main", "child"):
            self.assertTrue(all(body["model"] == "main-model" for body in self.bodies[route]))
        for body in self.bodies["review"]:
            self.assertEqual(body["model"], "review-model")
            self.assertFalse(body.get("tools"))
            self.assertNotIn(CHILD_MARKER, json.dumps(body))
        records = await self.app.sessions.context_records(task.thread_id, "parent_review")
        identity = records[0]["review_model"]
        self.assertEqual(identity["profile_id"], "review")
        self.assertEqual(identity["model"], "review-model")
        self.assertEqual(self.first_review_snapshot["review_model"], identity)
        self.assertEqual(self.first_review_snapshot["phase"], "independent")
        self.assertTrue(all(record["review_model"] == identity for record in records))
        self.assertNotIn("offline-review", json.dumps(identity))
        budget = await self.app.sessions.load_task_budget(task.id)
        self.assertEqual(budget.model_turns, 6)
        self.assertEqual((budget.input_tokens, budget.output_tokens), (300, 30))
        review_clients = [client for client in self.clients if client.route == "review"]
        self.assertEqual(len(review_clients), 1)
        await self.app.runtime_selection.use(reasoning_effort="high", idle=True)
        await asyncio.wait_for(review_clients[0].closed_event.wait(), timeout=2)
        self.assertTrue(review_clients[0].is_closed)
        self.streams["main"] = [[answer("After switch.")]]
        second, _ = await self.start("Explain briefly again.")
        self.assertEqual(second.status.value, "completed")
        self.assertEqual(len(self.bodies["review"]), 2)
        await self.app.aclose()
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_review_transport_failure_interrupts_task_and_resource_closes(self):
        with self.assertRaises(ModelStreamError):
            await self.review_run(fail=True)
        task = await self.app.sessions.load_task(self.last_task.id)
        self.assertEqual(task.status.value, "interrupted")
        self.assertEqual(len(self.bodies["review"]), 1)
        await self.app.aclose()
        self.assertTrue(all(client.is_closed for client in self.clients))


class ReviewModelLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_guard_clamps_limits_and_lazy_resource(self):
        parent_provider = ProviderConfig("https://parent.test", "parent", ApiProtocol.RESPONSES,
            api_key_env="KEY", timeout_s=5, max_retries=1)
        parent = ModelProfile("parent", parent_provider, 100000, 1000)
        review = ModelProfile("review", replace(parent_provider, model="review", timeout_s=30,
            max_retries=4), 80000, 500, context_policy=WindowPolicy(
                work_tokens=70000, safety_tokens=2000, task_tokens=90000), api_input_tokens=60000)
        allocated = []
        def allocate(provider):
            allocated.append(provider)
            raise AssertionError("unused review must remain lazy")
        base = BudgetedWindowClient(object(), object(), lambda: "thread",
            WindowPolicy(work_tokens=90000, safety_tokens=1000, task_tokens=100000),
            ApiContextLimits(100000, 1000, 75000), object(),
            constraints=RequestBudgetConstraints(host_prompt_tokens=65000))
        mode = type("Mode", (), {"effective_reasoning_effort": "high"})()
        route = ReviewModelFactory(review, allocate)(base, parent, mode)
        self.assertIs(route.client.sessions, base.sessions)
        self.assertIs(route.client.current_thread, base.current_thread)
        self.assertEqual(route.client.policy.work_tokens, 70000)
        self.assertEqual(route.client.policy.safety_tokens, 2000)
        self.assertEqual(route.client.policy.task_tokens, 90000)
        self.assertEqual(route.client.limits, ApiContextLimits(80000, 500, 60000))
        self.assertEqual(route.client.effective_input_cap(), 58000)
        self.assertEqual((route.identity["timeout_s"], route.identity["max_retries"]), (5, 1))
        await route.client.aclose()
        self.assertFalse(allocated)

    async def test_runtime_close_attempts_both_and_retries_failed_resource_only(self):
        class Resource:
            def __init__(self, fail=False):
                self.fail, self.calls = fail, 0
            async def aclose(self):
                self.calls += 1
                if self.fail:
                    self.fail = False
                    raise RuntimeError("close failure")
        main, review = Resource(), Resource(True)
        owned = RuntimeClients(main, review)
        with self.assertRaisesRegex(RuntimeError, "close failure"):
            await owned.aclose()
        self.assertEqual((main.calls, review.calls), (1, 1))
        await owned.aclose()
        await owned.aclose()
        self.assertEqual((main.calls, review.calls), (1, 2))


if __name__ == "__main__":
    unittest.main()
