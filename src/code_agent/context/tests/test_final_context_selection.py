from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.context.attachment_budget import message_tokens
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.task_state import TaskState
from code_agent.core.models import ContextBundle, Message, ToolDefinition
from code_agent.context._builder_support import _render_tools
from code_agent.context.repo_map import _StaleRepoContext
from code_agent.skills.registry import SkillContextBuilder
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard


class FinalContextSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    def builder(self, budget=4000, reports=None, enabled=True):
        config = ContextConfig(self.root, self.root, "System", repo_scan=100,
                               repo_map_tokens=budget, repo_map_enabled=enabled)
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        repo = RepoMapBuilder(files, config)
        self.addCleanup(repo.index.close)
        return WorkspaceContextBuilder(
            config, RuleLoader(guard, files, config), repo,
            DeterministicCompactor(config),
            debug_report=reports.append if reports is not None else None,
        )

    async def build(self, query, budget=4000, builder=None):
        reports = []
        builder = builder or self.builder(budget, reports)
        bundle = await builder.build("debug", (), query, (), TaskState.empty(), CancellationToken())
        return bundle, reports[0] if reports else None

    def l0(self, report):
        return [r for r in report["selected"] if r["tier"] == "L0"]

    async def test_l0_survives_neighbors_at_final_render_budget(self):
        self.write("target.py", "def target():\n" + "    value = '正文'\n" * 30 + "    return value\n")
        _, baseline = await self.build("repair target")
        budget = estimate_tokens(baseline["rendered_repo_context"])
        for i in range(8):
            self.write(f"caller_{i}.py", "from target import target\ndef caller():\n    return target()\n")
        bundle, report = await self.build("repair target", budget)
        self.assertEqual(report["rendered_repo_context"], baseline["rendered_repo_context"])
        self.assertIn("正文", self.l0(report)[0]["source"])
        self.assertTrue(any(d["status"] == "removed" for d in report["decisions"]))
        self.assertEqual(bundle.measurements["repo_context_estimated_tokens"], budget)

    async def test_oversized_source_downgrades_with_bounded_reads(self):
        self.write("target.py", "def target():\n" + "    value = 'long payload text'\n" * 80 + "    return value\n")
        bundle, report = await self.build("repair target", 500)
        self.assertEqual(self.l0(report), [])
        self.assertEqual(report["selected"][0]["tier"], "L1")
        self.assertEqual(report["decisions"][0]["status"], "downgraded")
        self.assertTrue(report["deferred"])
        self.assertTrue(all(r["end_line"] - r["start_line"] < 400 for r in report["deferred"]))
        self.assertLessEqual(bundle.measurements["repo_context_estimated_tokens"], 500)

    async def test_two_explicit_files_and_no_symbol_match(self):
        for path in ("a.py", "b.py"):
            self.write(path, "VALUE = 1\n" + "# header\n" * 45 + "def unrelated():\n    return 'outside module slice'\n")
        bundle, report = await self.build("inspect b.py and a.py")
        self.assertEqual([r["path"] for r in self.l0(report)], ["b.py", "a.py"])
        for record in self.l0(report):
            self.assertEqual((record["kind"], record["symbol"], record["range"]), ("line", "", [1, 40]))
            self.assertIn("VALUE = 1", record["source"])
        self.assertNotIn("outside module slice", bundle.system_prompt)

    async def test_task_ranking_reaches_final_anchor(self):
        self.write("app.py", "# widget behavior\nVALUE = 1\n")
        self.write("tests/check.py", "# widget behavior pytest regression\nVALUE = 2\n")
        self.write("docs/guide.md", "# widget behavior documentation manual\n")
        for query, expected in (("widget behavior", "app.py"),
                                ("pytest regression", "tests/check.py"),
                                ("documentation manual", "docs/guide.md")):
            with self.subTest(query=query):
                _, report = await self.build(query)
                self.assertEqual([r["path"] for r in self.l0(report)], [expected])

    async def test_ordinary_source_task_does_not_force_second_anchor(self):
        self.write("app.py", "def decodePayload():\n    return 1\n\ndef unrelated():\n    return 2\n")
        self.write("tests/check.py", "def check():\n    return 3\n")
        self.write("docs/guide.md", "# latest contest documentary\n")
        _, report = await self.build("repair decodePayload")
        self.assertEqual([(r["path"], r["symbol"]) for r in self.l0(report)], [("app.py", "decodePayload")])

    async def test_report_matches_final_prompt_and_cache_hit(self):
        self.write("target.py", "from helper import helper\ndef target():\n    return helper()\n")
        self.write("helper.py", "from leaf import leaf\ndef helper():\n    return leaf()\n")
        self.write("leaf.py", "def leaf():\n    return 1\n")
        reports = []
        builder = self.builder(reports=reports)
        first, _ = await self.build("repair target", builder=builder)
        again, _ = await self.build("repair target", builder=builder)
        report = reports[-1]
        self.assertEqual({r["tier"] for r in report["selected"]}, {"L0", "L1", "L2"})
        self.assertTrue(again.system_prompt.endswith(report["rendered_repo_context"]))
        self.assertEqual(first.system_prompt, again.system_prompt)
        self.assertEqual(reports[0]["selected"], report["selected"])
        self.assertEqual(reports[0]["decisions"], report["decisions"])
        self.assertEqual(again.measurements["cache_hits"], 1)
        m = again.measurements
        self.assertEqual(m["prompt_budget_tokens"], m["prompt_tokens"])
        self.assertEqual(m["repo_context_budget_tokens"], m["repo_map_tokens"])
        self.assertEqual(m["prompt_estimated_tokens"], estimate_tokens(again.system_prompt) + sum(map(message_tokens, again.messages)))
        self.assertEqual(m["repo_context_estimated_tokens"], estimate_tokens(report["rendered_repo_context"]))
        self.assertLessEqual(m["prompt_estimated_tokens"], m["prompt_budget_tokens"] - m["prompt_safety_tokens"])

    async def test_disabled_debug_does_not_change_prompt(self):
        self.write("app.py", "VALUE = 1\n")
        plain, _ = await self.build("inspect app.py", builder=self.builder())
        debug, _ = await self.build("inspect app.py")
        self.assertEqual(plain.system_prompt, debug.system_prompt)
        self.assertEqual(plain.measurements, debug.measurements)

    async def test_no_repo_context_has_zero_estimate(self):
        for enabled, query in ((True, "hello"), (False, "repair app.py")):
            reports = []
            bundle, _ = await self.build(query, builder=self.builder(reports=reports, enabled=enabled))
            self.assertEqual(bundle.measurements["repo_context_estimated_tokens"], 0)
            self.assertEqual(reports[0]["selected"], [])
            self.assertEqual(reports[0]["rendered_repo_context"], "")

    async def test_final_estimate_includes_tools_compacted_messages_and_skills(self):
        reports = []
        workspace = self.builder(reports=reports)
        builder = SkillContextBuilder(workspace, SimpleNamespace(render=lambda: "Active skill instruction"))
        tools = (ToolDefinition("inspect", "Inspect source", {"type": "object"}),)
        history = (Message("assistant", "old text " * 10000),)
        bundle = await builder.build("debug", history, "hello", tools, TaskState.empty(), CancellationToken())
        expected = (estimate_tokens(bundle.system_prompt) + estimate_tokens(_render_tools(tools))
                    + sum(map(message_tokens, bundle.messages)))
        self.assertEqual(bundle.measurements["prompt_estimated_tokens"], expected)
        self.assertGreater(expected, reports[0]["measurements"]["prompt_estimated_tokens"])
        self.assertLess(len(str(bundle.messages)), len(str(history)))
        self.assertEqual(dict(ContextBundle.from_dict(bundle.to_dict()).measurements), dict(bundle.measurements))

    async def test_tiny_budget_report_does_not_claim_l0_was_sent(self):
        self.write("target.py", "def target():\n    return 1\n")
        bundle, report = await self.build("repair target", 20)
        self.assertEqual(report["selected"], [])
        self.assertTrue(report["compact_fallback"])
        self.assertTrue(bundle.system_prompt.endswith(report["rendered_repo_context"]))
        self.assertLessEqual(bundle.measurements["repo_context_estimated_tokens"], 20)

    async def test_400_line_guard_has_distinct_debug_reason(self):
        self.write("target.py", "def target():\n" + "    value = 1\n" * 420)
        _, report = await self.build("repair target")
        self.assertEqual(self.l0(report), [])
        self.assertIn("400-line", report["decisions"][0]["reason"])
        self.assertTrue(all(r["end_line"] - r["start_line"] < 400 for r in report["deferred"]))

    async def test_stale_failure_does_not_reuse_previous_debug_selection(self):
        self.write("target.py", "def target():\n    return 1\n")
        reports = []
        builder = self.builder(reports=reports)
        await self.build("repair target", builder=builder)
        with patch("code_agent.context.repo_map._read_l0_sources", side_effect=_StaleRepoContext(("target.py",))):
            bundle, _ = await self.build("repair target", builder=builder)
        self.assertTrue(reports[0]["selected"])
        self.assertEqual(reports[1]["selected"], [])
        self.assertEqual(bundle.measurements["repo_context_estimated_tokens"], 0)
