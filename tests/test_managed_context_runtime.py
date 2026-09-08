import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from code_agent.config.loader import load_runtime_config
from code_agent.config.context_policy import configured_context_policy
from code_agent.context.models import ContextConfig
from code_agent.context.rules import RuleLoader
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context_windows.policy import WindowPolicy
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.core.context_request import ContextRequest
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent_win.managed_context import build_managed_context, wire_managed_engine
from code_agent_win.runtime_extensions import ThreadRuntimeBinding


class Skills:
    async def restore(self, thread):
        pass
    def activation(self, thread):
        return SimpleNamespace(render=lambda: "LATE_SKILL_CONTENT " * 120)


class ManagedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_prefix_is_counted_and_same_guard_is_wired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = load_runtime_config(env={"CHAOS_CONFIG":str(root/"missing.toml"),
                "CHAOS_API":"responses", "CHAOS_BASE_URL":"https://example.test", "CHAOS_MODEL":"test",
                "CHAOS_API_KEY_ENV":"KEY"})
            profile = replace(runtime.profiles[0], context_policy=WindowPolicy(work_tokens=32000))
            sessions=SQLiteSessionRepository(root/"sessions.db")
            thread=await sessions.create_thread()
            await sessions.append_message(thread,Message("user","Review a small function"))
            guard=WorkspacePathGuard(root)
            files=WorkspaceFiles(guard,IgnoreRules.from_workspace(root))
            config=ContextConfig(root,root,"BASE_SYSTEM",repo_map_enabled=False)
            binding=ThreadRuntimeBinding()
            context=build_managed_context(config,RuleLoader(guard,files,config),RepoMapBuilder(files,config),
                Skills(),sessions,binding,object(),profile)
            bundle=await context.build(ContextRequest(thread,1,(),"",(),TaskState(),CancellationToken()))
            self.assertIn("LATE_SKILL_CONTENT",bundle.system_prompt)
            self.assertGreater(bundle.measurements["prompt_tokens"],2000)
            dispatcher=SimpleNamespace()
            model=wire_managed_engine(object(),SimpleNamespace(_inner=context),dispatcher)
            self.assertIsInstance(model,BudgetedWindowClient)
            self.assertIs(dispatcher.context_actions,context.context_actions)

    def test_policy_is_opt_in_and_has_independent_task_budget(self):
        self.assertIsNone(configured_context_policy({}))
        policy=configured_context_policy({"context_policy":{"work_tokens":128000,"task_tokens":5000000}})
        self.assertEqual(policy.work_tokens,128000)
        self.assertEqual(policy.task_tokens,5000000)
        with self.assertRaises(ValueError):
            configured_context_policy({"context_policy":{"automatic_expand":True}})
