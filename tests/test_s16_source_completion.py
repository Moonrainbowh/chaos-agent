"""S16 regressions through public task start and the original child composition.

Only the external HTTP boundary is scripted; context, tools, policy, Sessions,
delegation, child factory, engine and ChildResult remain production objects.
"""
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from code_agent.core.events import EventKind
from code_agent.core.completion_contract import TaskIntent
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.models import AgentContribution, PluginContributions, PluginManifest
from code_agent.plugins.registry import PluginRegistryBuilder
from code_agent.providers.config import ApiProtocol, ConfiguredApiKey, ProviderConfig
from code_agent.providers.openai_responses import OpenAIResponsesClient
from tests.agent_app_test_support import _isolated_application
from chaos_agent.tool_support import _SOURCE_REVIEW_GUIDANCE, windows_system_prompt


FIXTURE = Path(__file__).parent / "fixtures" / "s16-v7.json"
MARKERS = ("S16_ROLE_ALPHA_49177", "S16_ROLE_BETA_82819")


def call(identifier, name, arguments, index=0):
    return {"type": "response.output_item.done", "output_index": index,
            "item": {"type": "function_call", "id": "fc-" + identifier,
                     "call_id": identifier, "name": name,
                     "arguments": json.dumps(arguments)}}


def answer(text):
    return {"type": "response.output_text.delta", "delta": text}


class OfflineProvider:
    """Real OpenAI preparation/serialization/parser, zero network transport."""
    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        config = ProviderConfig("https://api.example.test", "s16-offline",
            ApiProtocol.RESPONSES, api_key_env=None,
            api_key_source=ConfiguredApiKey("offline-test-key"), max_retries=0)

        def handle(request):
            self.bodies.append(json.loads(request.content))
            if not self.streams:
                raise AssertionError("unexpected extra model request")
            events = self.streams.pop(0) + [{"type": "response.completed",
                "response": {"usage": {"input_tokens": 50, "output_tokens": 5}}}]
            return httpx.Response(200, content="".join(
                "data: " + json.dumps(event) + "\n\n" for event in events))

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.client = OpenAIResponsesClient(config, http_client=self.http)

    async def close(self):
        await self.client.aclose()
        await self.http.aclose()


class S16ProductionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory()
        self.app, self.root, _ = _isolated_application(Path(self.temp.name))
        self.providers = []
        (self.root / "AGENTS.md").write_text("Preserve workspace constraints.", encoding="utf-8")
        for path, content in self.fixture["sources"].items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        agents = tuple(AgentContribution(name, AgentMode.MEDIUM, instructions,
                       ("read_file", "search_text"), False)
                       for name, instructions in (("alpha", MARKERS[0]), ("beta", MARKERS[1]),
                           ("sourceaudit", self.fixture["agent_instructions"])))
        manifest = PluginManifest("s16-test", "s16", "1.0.0", "1",
            hashlib.sha256(b"s16-regression").hexdigest(), "offline-fixture", True, True,
            PluginContributions(agents=agents))
        snapshot = PluginRegistryBuilder(self.app.subagents._modes).build((manifest,))
        self.assertEqual(snapshot.errors, ())
        self.app.plugins.stage(snapshot)
        self.assertTrue(self.app.plugins.apply(task_active=False))
        with patch("chaos_agent.app._model_client", return_value=object()):
            await self.app.tui.modes.use("medium", idle=True)
            await self.app.runtime_selection.use(topology="team", idle=True)

    async def asyncTearDown(self):
        await self.app.aclose()
        for provider in self.providers:
            await provider.close()
        self.temp.cleanup()

    def provider(self, streams):
        provider = OfflineProvider(streams)
        self.providers.append(provider)
        return provider

    async def run_parent(self, prompt, parent_streams, child_streams=()):
        if child_streams:
            parent_streams = [[call("disclose-delegate", "load_tool_contract",
                                    {"name": "delegate_agent"})]] + parent_streams
        parent = self.provider(parent_streams)
        children = [self.provider(streams) for streams in child_streams]
        self.app.controller._engine._model.model = parent.client
        factory = self.app.subagents._runner._factory.__self__
        allocated = iter(children)
        factory._client_factory = lambda *args, **kwargs: next(allocated).client
        task = await self.app.tasks.start(prompt)
        events = [event async for event in self.app.tasks.events(task.id)]
        messages = await self.app.sessions.load_messages(task.thread_id)
        results = [json.loads(message.content)["output"] for message in messages
                   if message.role == "tool" and message.name == "delegate_agent"]
        artifact_root = os.getenv("S16_P0_ARTIFACT_DIR")
        if artifact_root:
            target = Path(artifact_root) / (self._testMethodName + "-" + str(len(self.providers)) + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"parent_contract": task.contract.to_dict(),
                "parent_events": [event.to_dict() for event in events],
                "parent_wire_bodies": parent.bodies,
                "child_wire_bodies": [provider.bodies for provider in children],
                "child_threads": self.app.subagents._child_threads,
                "child_results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        return await self.app.sessions.load_task(task.id), events, parent, children, results

    async def test_role_instructions_reach_both_child_requests_without_sibling_leak(self):
        parent_streams = [
            [call("alpha-delegate", "delegate_agent", {"agent_id": "s16.alpha",
                 "objective": "Read names.py and report.", "token_budget": 60000}),
             call("beta-delegate", "delegate_agent", {"agent_id": "s16.beta",
                 "objective": "Read names.py and report.", "token_budget": 60000}, index=1)],
            [answer("Analysis delivered.")]]
        child = [[call("read-source", "read", {"operation": "file", "path": "names.py"})],
                 [answer("Source analyzed.")]]
        task, events, parent, children, results = await self.run_parent(
            "Read only: deep source investigation; delegate two independent source analyses.", parent_streams, (child, child))
        self.assertEqual(len(results), 2, [event.to_dict() for event in events])
        self.assertEqual([result.get("status") for result in results], ["completed", "completed"], results)
        self.assertEqual(len(self.app.subagents._child_threads), 2)
        self.assertEqual([len(provider.bodies) for provider in children], [2, 2])
        for index, provider in enumerate(children):
            for body in provider.bodies:
                with self.subTest(child=index, request=provider.bodies.index(body)):
                    self.assertTrue(MARKERS[index] in body["instructions"],
                        "RED: AgentDefinition.instructions role marker missing from actual prepared body")
                    self.assertNotIn(MARKERS[1 - index], json.dumps(body))
                    self.assertNotIn(MARKERS[index], json.dumps(body["input"]))
        self.assertNotIn(MARKERS[0], json.dumps(parent.bodies))
        self.assertNotIn(MARKERS[1], json.dumps(parent.bodies))
        # Request capture proves prompt delivery and continuity, not semantic quality.
        for provider in (parent, *children):
            for body in provider.bodies:
                self.assertIn(_SOURCE_REVIEW_GUIDANCE, body["instructions"])
                self.assertNotIn(_SOURCE_REVIEW_GUIDANCE, json.dumps(body["input"]))

    def test_shared_source_review_guidance_is_platform_independent(self):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform), patch("chaos_agent.tool_support.os.name", platform):
                prompt = windows_system_prompt(git_available=False)
                self.assertEqual(prompt.count(_SOURCE_REVIEW_GUIDANCE), 1)

    async def test_original_v7_parent_prompt_is_frozen_as_analyze_at_public_start(self):
        task, _, _, _, _ = await self.run_parent(self.fixture["parent_prompt"], [[answer("Analysis.")]])
        self.assertEqual(task.contract.objective, self.fixture["parent_prompt"])
        self.assertEqual(task.contract.intent.value, "analyze")

    async def test_read_only_spelling_and_explicit_modification_filename_controls(self):
        cases = (("Read only: inspect test_names.py", "analyze"),
                 ("Readonly: inspect test_names.py", "analyze"),
                 ("Read-only. Inspect names.py.", "analyze"),
                 ("This task is read-only.", "analyze"),
                 ("请帮我修复 read-only 模式不生效的问题", "modify"),
                 ("Can you fix read-only behavior in the editor?", "modify"),
                 ("Please help me fix read-only behavior in the editor.", "modify"),
                 ("Modify read-only.md to correct its typo.", "modify"),
                 ("Modify readonly.md to correct its typo.", "modify"))
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                task, _, _, _, _ = await self.run_parent(prompt, [[answer("Reported.")]])
                self.assertEqual(task.contract.intent.value, expected)

    async def test_builtin_review_role_reaches_actual_body_without_write_tools(self):
        _, _, _, children, results = await self.run_parent("Read only: review names.py.",
            [[call("builtin-review", "delegate_agent", {"role": "review",
                "objective": "Review names.py", "token_budget": 60000})], [answer("Reported.")]],
            ([[call("review-read", "read", {"operation": "file", "path": "names.py"})],
              [answer("Review completed.")]],))
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(len(children[0].bodies), 2)
        for body in children[0].bodies:
            self.assertIn("Review for defects, regressions, and missing tests. Return findings first.",
                          body["instructions"])
            names = {tool["name"] for tool in body["tools"]}
            self.assertTrue(names.isdisjoint({"write", "execute", "delegate_agent"}))
            for tool in body["tools"]:
                if tool["name"] == "edit":
                    self.assertEqual(tool["parameters"]["properties"]["operation"]["enum"], ["plan"])

    async def test_resumed_legacy_contract_keeps_its_original_modify_intent(self):
        parent = self.provider([[answer("Legacy task advisory.")]])
        self.app.controller._engine._model.model = parent.client
        newly_frozen = await self.app.tasks.start(self.fixture["parent_prompt"])
        legacy_thread = await self.app.sessions.create_thread()
        legacy = await self.app.sessions.create_task(legacy_thread,
            replace(newly_frozen.contract, intent=TaskIntent.MODIFY))
        _ = [event async for event in self.app.tasks.events(legacy.id)]
        persisted = await self.app.sessions.load_task(legacy.id)
        self.assertEqual(persisted.contract.intent, TaskIntent.MODIFY)
        self.assertEqual(persisted.contract.objective, self.fixture["parent_prompt"])

    async def test_loader_description_distinguishes_model_requests_from_user_messages(self):
        _, _, parent, _, _ = await self.run_parent("Read only: explain tool availability.", [[answer("Explained.")]])
        loader = next(tool for tool in parent.bodies[0]["tools"] if tool["name"] == "load_tool_contract")
        summary = loader["description"].split("\n", 1)[0]
        self.assertIn("next model request", summary)
        self.assertIn("not next user message", summary)
        self.assertLessEqual(len(summary), 120)

    async def test_new_fixture_child_reads_four_sources_directly_with_frozen_original_bytes(self):
        helper_path = Path(__file__).parents[1] / "docs/next-version/s16-source-completion/s16_fixture.py"
        spec = importlib.util.spec_from_file_location("s16_fixture", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for path in (*self.fixture["sources"], "AGENTS.md"):
            (self.root / path).unlink()
        fixture = helper.prepare_workspace(self.root)
        agent = AgentContribution("sourceaudit", AgentMode.MEDIUM, fixture["agent_instructions"],
                                  ("read_file", "read_code_slices", "list_files", "search_text"), False)
        manifest = PluginManifest("s16-fixture", "s16", "1.0.0", "1",
            hashlib.sha256(b"s16-p2-fixture").hexdigest(), "fixture", True, True,
            PluginContributions(agents=(agent,)))
        self.app.plugins.stage(PluginRegistryBuilder(self.app.subagents._modes).build((manifest,)))
        self.assertTrue(self.app.plugins.apply(task_active=False))
        for path, expected in self.fixture["source_sha256"].items():
            self.assertEqual(hashlib.sha256((self.root / path).read_bytes()).hexdigest(), expected)
        shared = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("delegate_agent", shared)
        self.assertNotIn("child_objective", shared)
        self.assertEqual(fixture["required_source_paths"], list(self.fixture["sources"]))
        for text in (shared, fixture["parent_input"], fixture["child_objective"], fixture["agent_instructions"]):
            self.assertNotIn("Preserve order and duplicates", text)
            self.assertNotIn("return list(values)", text)
            self.assertNotIn("one disclosure+four reads", text)
        calls = [call("p2-read-" + str(index), "read", {"operation": "file", "path": path}, index=index)
                 for index, path in enumerate(fixture["required_source_paths"])]
        task, _, parent, children, results = await self.run_parent(fixture["parent_input"],
            [[call("p2-delegate", "delegate_agent", {"agent_id": fixture["agent_id"],
                "objective": fixture["child_objective"], "token_budget": fixture["runtime"]["child_token_budget"],
                "required_sources": fixture["required_source_paths"],
                "tool_budget": fixture["runtime"]["child_tool_budget"],
                "active_seconds": fixture["runtime"]["child_active_seconds"]})], [answer("Source advisory checked.")]],
            ((calls, [answer("Offline synthetic source analysis; unverified.")]),))
        self.assertEqual(task.contract.intent, TaskIntent.ANALYZE)
        self.assertEqual(len(parent.bodies), 3)
        self.assertEqual(parent.streams, [])
        self.assertEqual(task.status.value, 'completed')
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["usage"]["tool_calls"], 4)
        self.assertEqual(len(children[0].bodies), 2)
        self.assertIn("read", {tool["name"] for tool in children[0].bodies[0]["tools"]})
        self.assertIn(fixture["agent_instructions"], children[0].bodies[0]["instructions"])
        child_id = next(iter(self.app.subagents._child_threads.values()))
        child_messages = await self.app.sessions.load_messages(child_id)
        child_calls = [tool for message in child_messages for tool in message.tool_calls]
        self.assertEqual([tool.name for tool in child_calls], ["read"] * 4)
        self.assertEqual([tool.arguments["path"] for tool in child_calls], fixture["required_source_paths"])
        self.assertNotIn("Then delegate exactly once", children[0].bodies[0]["instructions"])
        self.assertEqual(len([message for message in child_messages if message.role == "tool"]), 4)
        read_results = [json.loads(item["output"]) for item in children[0].bodies[1]["input"]
                        if item.get("type") == "function_call_output"]
        self.assertEqual(len(read_results), 4)
        for path, result in zip(fixture["required_source_paths"], read_results):
            self.assertFalse(result["is_error"])
            self.assertEqual(result["output"]["text"], (self.root / path).read_bytes().decode("utf-8"))

    async def test_original_v7_child_cannot_complete_after_disclosure_with_zero_reads(self):
        streams = []
        for turn in self.fixture["child_turns"]:
            converted = []
            for event in turn:
                if event["kind"] == "tool_call":
                    tool = event["tool_call"]
                    converted.append(call(tool["id"], tool["name"], tool["arguments"]))
                elif event["kind"] == "text_delta":
                    converted.append(answer(event["text"]))
            streams.append(converted)
        streams.append([answer("I still have not read the required files.")])
        task, events, _, children, results = await self.run_parent(
            self.fixture["parent_prompt"], [[call("v7-delegate", "delegate_agent",
                {"agent_id": "s16.sourceaudit", "objective": self.fixture["child_objective"],
                 "token_budget": 300000, "tool_budget": 5, "active_seconds": 240,
                 "required_sources": list(self.fixture["sources"])})],
                [answer("Parent reports child advisory.")]], (streams,))
        self.assertEqual(len(results), 1, [event.to_dict() for event in events])
        self.assertEqual(len(children[0].bodies), 3)
        child_id = next(iter(self.app.subagents._child_threads.values()))
        relation = await self.app.sessions.load_thread_relation(child_id)
        self.assertEqual(relation.parent_thread_id, task.thread_id)
        messages = await self.app.sessions.load_messages(child_id)
        calls = [tool for message in messages for tool in message.tool_calls]
        self.assertEqual([tool.name for tool in calls], ["load_tool_contract"])
        self.assertTrue(any(message.role == "tool" for message in messages))
        self.assertTrue(any(event.kind is EventKind.ACTION_COMPLETED for event in events))
        self.assertNotEqual(results[0]["status"], "completed",
            "RED: production ChildResult reports completion after disclosure with zero source reads")
        self.assertEqual(results[0]["error"], "source_requirements_unmet")
        self.assertEqual(results[0]["result"]["remaining"], list(self.fixture["sources"]))
        self.assertEqual(children[0].streams, [])

    async def test_team_parent_natural_lease_gets_real_correction_then_source_failure(self):
        streams = []
        for turn in self.fixture["child_turns"]:
            converted = []
            for event in turn:
                if event["kind"] == "tool_call":
                    tool = event["tool_call"]
                    converted.append(call(tool["id"], tool["name"], tool["arguments"]))
                elif event["kind"] == "text_delta":
                    converted.append(answer(event["text"]))
            streams.append(converted)
        streams.append([answer("Still missing the sources after Host correction.")])
        _, _, _, children, results = await self.run_parent(self.fixture["parent_prompt"],
            [[call("v7-standard-delegate", "delegate_agent", {"agent_id": "s16.sourceaudit",
                "objective": self.fixture["child_objective"], "token_budget": 300000, "tool_budget": 5,
                "required_sources": list(self.fixture["sources"])})], [answer("Checked failure.")]],
            (streams,))
        self.assertEqual(len(children[0].bodies), 3)
        self.assertEqual(children[0].streams, [])
        self.assertEqual(results[0]["error"], "source_requirements_unmet")
        self.assertEqual(results[0]["result"]["remaining"], list(self.fixture["sources"]))
        self.assertTrue(any(item.get('role') == 'developer' and 'required full source' in item.get('content', '')
                            for item in children[0].bodies[2]['input']))

    async def test_source_gate_corrects_then_reads_and_completes(self):
        streams = [[answer("I will read next.")],
                   [call("corrected-read", "read", {"operation": "file", "path": "names.py"})],
                   [answer("Actual source advisory.")]]
        _, _, _, children, results = await self.run_parent("Read only: inspect names.py.",
            [[call("corrected-delegate", "delegate_agent", {"agent_id": "s16.alpha",
                "objective": "Read names.py", "required_sources": ["names.py"], "token_budget": 60000})],
             [answer("Checked.")]], (streams,))
        self.assertEqual(results[0]["status"], "completed", results)
        self.assertEqual(len(children[0].bodies), 3)
        self.assertEqual(results[0]["usage"]["tool_calls"], 1)
        self.assertNotEqual(results[0]["result"]["verification_status"], "verified")

    async def test_partial_and_duplicate_source_reads_do_not_hide_remaining(self):
        streams = [[call("partial-first", "read", {"operation": "file", "path": "names.py"})],
                   [answer("Finished?")],
                   [call("partial-repeat", "read", {"operation": "file", "path": "names.py"})],
                   [answer("No additional source read.")]]
        _, _, _, children, results = await self.run_parent("Read only: inspect sources.",
            [[call("partial-delegate", "delegate_agent", {"agent_id": "s16.alpha",
                "objective": "Read sources", "required_sources": ["names.py", "test_names.py"],
                "token_budget": 60000})], [answer("Checked.")]], (streams,))
        self.assertEqual(results[0]["status"], "failed", results)
        self.assertEqual(results[0]["error"], "source_requirements_unmet")
        self.assertEqual(results[0]["result"]["remaining"], ["test_names.py"])
        self.assertEqual(len(children[0].bodies), 4)
