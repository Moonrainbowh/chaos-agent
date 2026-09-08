"""Real host/engine loop with a deterministic model; no live API quality claim."""
import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from code_agent.capabilities import CapabilityStrategy
from code_agent.config.loader import load_runtime_config
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.managed_context import build_managed_context, wire_managed_engine
from code_agent_win.runtime_extensions import ThreadRuntimeBinding


class Skills:
    async def restore(self, thread):
        pass

    def activation(self, thread):
        return SimpleNamespace(render=lambda: "")


class ScriptedModel:
    def __init__(self):
        self.step, self.calls = 0, 0
        self.reference = {}
        self.errors = []
        self.prompts = []

    async def stream(self, system, messages, tools):
        self.calls += 1
        self.prompts.append((system, messages))
        for message in messages:
            if message.role == "tool" and '"is_error":true' in message.content:
                self.errors.append(message.content)
        if self.step == 1 and not self.reference:
            evidence = next(message for message in messages if message.name == "read_file")
            matches = re.findall(r'\[history_ref window=(\w+) item=(\w+)\]', evidence.content)
            if matches:
                self.reference = dict(zip(("window_id", "item_id"), matches[-1]))
        actions = (
            ("read_file", {"path": "source-evidence.txt"}),
            ("notes_write_file", {"path": "checkpoint.md", "text": json.dumps(self.reference)}),
            ("new_context", {}),
            ("notes_read_file", {"path": "checkpoint.md"}),
            ("history_read_item", self.reference),
            ("new_context", {}),
            ("history_search_contents", {"query": "source-evidence.txt"}),
            ("history_list_windows", {}),
        )
        if self.step < len(actions):
            name, args = actions[self.step]
            if name not in {t.name for t in tools}:
                name, args = "load_tool_contract", {"name": name}
            else:
                self.step += 1
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall(f"call{self.calls}", name, args))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Recovered the persisted evidence.")
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(100, 20))
        yield ModelEvent(ModelEventKind.COMPLETED)


class PersistentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_factory_dispatch_policy_and_engine_across_two_resets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source-evidence.txt").write_text("ORIGINAL_EVIDENCE_42", encoding="utf-8")
            runtime = load_runtime_config(env={"CHAOS_CONFIG": str(root / "missing.toml"),
                "CHAOS_API": "responses", "CHAOS_BASE_URL": "https://example.test",
                "CHAOS_MODEL": "test", "CHAOS_API_KEY_ENV": "KEY"})
            profile = replace(runtime.profiles[0],
                              context_policy=WindowPolicy(strategy="persistent", work_tokens=100000))
            repo = SQLiteSessionRepository(root / "sessions.db")
            thread = await repo.create_thread()
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            config = ContextConfig(root, root, "BASE_RULES", repo_map_enabled=False)
            binding, raw = ThreadRuntimeBinding(), ScriptedModel()
            context = build_managed_context(config, RuleLoader(guard, files, config), RepoMapBuilder(files, config),
                                            Skills(), repo, binding, raw, profile)
            dispatcher = RootActionDispatcher(files, WorkspaceEditor(guard),
                ActionPolicy(PolicyConfig(approval_mode=ApprovalMode.FULL_LOCAL, workspace_root=root)), ApprovalBroker())
            model = wire_managed_engine(raw, context, dispatcher)
            names = {tool.name for tool in dispatcher.tools()}
            self.assertIn("notes_append_to_file", names)
            self.assertNotIn("context_note", names)
            engine = AgentEngine(model, context, dispatcher, repo,
                limits=EngineLimits(40, 100, 10, 5000000), capability_strategy=CapabilityStrategy.HYBRID)
            token = binding.bind(thread)
            try:
                events = [event async for event in engine.run("Recover the evidence across windows.", thread_id=thread)]
            finally:
                binding.reset(token)
            self.assertFalse(raw.errors)
            self.assertEqual(raw.step, 8)
            self.assertIn(EventKind.COMPLETED, [event.kind for event in events])
            windows = await repo.context_records(thread, "window")
            self.assertEqual(len(windows), 2)
            usage = await repo.context_records(thread, "usage")
            self.assertEqual(len(usage), raw.calls)
            self.assertEqual({entry["purpose"] for entry in usage}, {"main"})
            self.assertEqual(sum(entry["charged"] for entry in usage), raw.calls * 120)
            results = [message for message in await repo.load_messages(thread) if message.role == "tool"]
            recovered = next(message for message in results if message.name == "history_read_item")
            self.assertIn("ORIGINAL_EVIDENCE_42", recovered.content)
            self.assertTrue(all("Context strategy: persistent" in system for system, _ in raw.prompts))
