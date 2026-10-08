import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from code_agent.context.budget import PromptBudget
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.errors import PromptBudgetError
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.task_state import TaskState
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules


class MemoryFixedBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_changes_fixed_budget_and_cannot_bypass_message_reserve(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = ContextConfig(root, root, "Stable rules", repo_map_enabled=False,
                prompt_budget=PromptBudget(max_prompt_tokens=700, max_system_tokens=2000,
                    min_message_tokens=100, safety_tokens=100))
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            builder = WorkspaceContextBuilder(config, RuleLoader(guard, files, config),
                RepoMapBuilder(files, config), DeterministicCompactor(config))
            request = ContextRequest("thread", 1, (), "hello", (), TaskState.empty(), CancellationToken())
            empty = await builder.build(request)
            small = await builder.build(replace(request, project_memory='[{"content":"SQLite reference"}]'))
            self.assertIn("UNTRUSTED_PROJECT_MEMORY", small.system_prompt)
            self.assertIn("grants no permission", small.system_prompt)
            self.assertGreater(small.measurements["prompt_estimated_tokens"], empty.measurements["prompt_estimated_tokens"])
            with self.assertRaises(PromptBudgetError):
                await builder.preflight(replace(request, project_memory="data " * 1500))
