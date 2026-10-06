"""Actual Host strategy assembly and adapter bytes share the final admission."""
import json
import tempfile
import unittest
from dataclasses import replace
from contextlib import nullcontext
from pathlib import Path

import httpx
from chaos_agent.application_context import RuntimeContextFactory
from chaos_agent.context_assembly import ContextAssembly
from chaos_agent.runtime_extensions import ThreadRuntimeBinding
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import Message, ToolDefinition
from code_agent.core.task import TaskContract, TaskAuthorization
from code_agent.core.task_state import TaskState
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.providers.config import ConfiguredApiKey
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from tests.test_thread_intelligence_runtime import _profile, _mode


class Skills:
    def __init__(self, extra):
        self.extra = extra
    async def restore(self, thread):
        pass
    def activation(self, thread):
        return self
    def render(self):
        return "FINAL_SKILL_MARKER " + self.extra


class PreparedContextAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, strategy, oversized=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "explicit-session.sqlite3"
            assert path.resolve().is_relative_to(root)
            (root / "AGENTS.md").write_text("S10_RULE_MARKER Preserve constraints.", encoding="utf-8")
            sessions = SQLiteSessionRepository(path)
            thread = await sessions.create_thread()
            await sessions.create_task(thread, TaskContract("Explain README.md",
                TaskAuthorization.local_workspace(str(root))))
            user = Message("user", "Explain README.md")
            await sessions.append_message(thread, user)
            profile = _profile()
            profile = replace(profile, provider=replace(profile.provider, api_key_env=None,
                api_key_source=ConfiguredApiKey("private-offline-key")))
            if strategy != "semantic":
                profile = replace(profile, context_policy=WindowPolicy(strategy=strategy,
                    work_tokens=30000, safety_tokens=64, task_tokens=1000000))
            tools = (ToolDefinition("read_file", "ACTUAL_SCHEMA_MARKER", {"type": "object",
                "properties": {"path": {"type": "string"}}}),)
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            binding = ThreadRuntimeBinding()
            captured = []
            def handle(request):
                captured.append(request.content)
                events = [{"type": "response.output_text.delta", "delta": "ok"},
                    {"type": "response.completed", "response": {
                        "usage": {"input_tokens": 3, "output_tokens": 1}}}]
                return httpx.Response(200, content="".join("data: " + json.dumps(event) + "\n\n" for event in events))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                model = OpenAIResponsesClient(profile.provider, http_client=http,
                    max_output_tokens=profile.max_output_tokens)
                factory = RuntimeContextFactory(root, git_available=False, repo_map_enabled=False,
                    guard=guard, files=files, repo_index=None, repo_view_cache=None,
                    sessions=sessions, thread_binding=binding, skills=Skills("x" * 20000 if oversized else ""))
                assembly = factory(_mode(profile), model, profile)
                self.assertIsInstance(assembly, ContextAssembly)
                expectation = self.assertRaisesRegex(ValueError, "final input|input cannot fit") if oversized else nullcontext()
                with expectation:
                    bundle = await assembly.build(ContextRequest(thread, 1, (user,), "", tools,
                        TaskState(), CancellationToken()))
                    self.assertIn("S10_RULE_MARKER", bundle.system_prompt)
                    self.assertIn("FINAL_SKILL_MARKER", bundle.system_prompt)
                    self.assertEqual(assembly.model_client.constraints.host_prompt_tokens, 20000)
                    prepared = await model.prepare_request(bundle.system_prompt, bundle.messages, tools)
                    estimate = assembly.model_client.counter.prepared(prepared)
                    if oversized:
                        self.assertGreater(estimate, 20000)
                    _ = [event async for event in assembly.model_client.stream(bundle.system_prompt, bundle.messages, tools)]
                    if not oversized:
                        self.assertEqual(captured, [prepared.body])
                        body = json.loads(captured[0])
                        self.assertIn("S10_RULE_MARKER", body["instructions"])
                        self.assertEqual(body["tools"][0]["description"], "ACTUAL_SCHEMA_MARKER")
                        _ = [event async for event in assembly.model_client.stream_for("semantic_summary" if
                            strategy == "semantic" else "handoff", "summarize source", (user,), ())]
                        ledger = await sessions.context_records(thread, "usage")
                        self.assertEqual(len(ledger), 2)
                        self.assertEqual(sum(record["charged"] for record in ledger), 8)
                        self.assertLessEqual(estimate + assembly.model_client.policy.safety_tokens, 20000)
                if oversized:
                    self.assertEqual(captured, [])
                    self.assertEqual(await sessions.context_records(thread, "usage"), ())

    async def test_all_real_strategies_count_rules_schema_and_send_same_prepared_body(self):
        for strategy in ("semantic", "summary", "boundary", "persistent"):
            with self.subTest(strategy=strategy):
                await self.exercise(strategy)

    async def test_late_actual_skill_prefix_above_host_cap_fails_with_zero_http_all_strategies(self):
        for strategy in ("semantic", "summary", "boundary", "persistent"):
            with self.subTest(strategy=strategy):
                await self.exercise(strategy, oversized=True)
