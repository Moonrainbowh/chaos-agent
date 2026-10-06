"""Scripted Provider traces over the actual default Application task path."""
import tempfile
import unittest
from pathlib import Path

from code_agent.core.events import EventKind
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskStatus
from tests.agent_app_test_support import _isolated_application


class ScriptModel:
    def __init__(self, calls, *, repeated_text=False):
        self.script = calls
        self.requests = 0
        self.repeated_text = repeated_text

    async def stream(self, *args):
        index = self.requests
        self.requests += 1
        if index < len(self.script):
            name, arguments = self.script[index]
            if self.repeated_text:
                yield ModelEvent(ModelEventKind.TEXT_DELTA, text="继续查看")
            yield ModelEvent(ModelEventKind.TOOL_CALL,
                tool_call=ToolCall(f"script-{index}", name, arguments))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="调查结果来自实际读取；未运行验证。")
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 3))
        yield ModelEvent(ModelEventKind.COMPLETED)


class HostProgressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        container = Path(self.temp.name).resolve()
        self.assertTrue((container / "state/sessions.sqlite3").resolve().is_relative_to(container))
        # The trace tests progress, with a known counter rather than the unknown
        # model's intentionally conservative UTF-8 byte upper bound.
        self.app, self.root, _ = _isolated_application(container, model_name="gpt-4.1")
        self.assertIsNotNone(self.app.controller._engine._model.counter.encoding)
        (self.root / "src").mkdir()
        for i in range(24):
            (self.root / "src" / f"unit_{i}.py").write_text(f"value_{i} = {i}\n", encoding="utf-8")
            (self.root / "src" / f"unit_{i}.md").write_text(f"Value {i}\n", encoding="utf-8")

    async def asyncTearDown(self):
        await self.app.aclose()
        self.temp.cleanup()

    async def run_trace(self, objective, calls, repeated_text=False):
        model = ScriptModel(calls, repeated_text=repeated_text)
        self.app.controller._engine._model.model = model
        task = await self.app.tasks.start(objective)
        initial = await self.app.sessions.load_task_budget(task.id)
        self.assertFalse(initial.lease_final_extension)
        self.assertLessEqual(initial.lease_model_turn_limit, initial.limits.max_agent_rounds)
        events = [event async for event in self.app.tasks.events(task.id)]
        final = await self.app.sessions.load_task(task.id)
        budget = await self.app.sessions.load_task_budget(task.id)
        return task, final, budget, model, events

    def read(self, i):
        return "read", {"operation": "file", "path": f"src/unit_{i}.py"}

    async def test_long_related_read_only_trace_without_prose_passes_five_turns(self):
        _, final, budget, model, events = await self.run_trace(
            "解释 src/ 内各模块，给出只读调查结果", [self.read(i) for i in range(16)])
        completed = [e for e in events if e.kind is EventKind.ACTION_COMPLETED]
        self.assertEqual(len(completed), 16)
        self.assertTrue(all(not e.payload["result"]["is_error"] for e in completed))
        self.assertEqual(model.requests, 17)
        self.assertGreater(budget.lease_renewals, 0)
        self.assertEqual(final.status, TaskStatus.COMPLETED)
        self.assertFalse(any(e.kind is EventKind.TASK_BUDGET_WARNING and
            e.payload.get("category") == "exploration" for e in events))

    async def test_unrelated_new_files_do_not_renew_even_with_repeated_prose(self):
        _, final, budget, model, _ = await self.run_trace(
            "解释 authentication 子系统，只读", [self.read(i) for i in range(24)], True)
        self.assertEqual(budget.lease_renewals, 0)
        self.assertLess(model.requests, 24)
        self.assertNotEqual(final.status, TaskStatus.RUNNING)

    async def test_negated_whole_repository_does_not_renew_for_unrelated_reads(self):
        for objective in (
            "Do not inspect the whole repository; explain src/auth/ only.",
            "不要检查全仓库；只解释 src/auth/。",
            "Explain src/auth/ only; do not inspect src/.",
        ):
            with self.subTest(objective=objective):
                _, final, budget, model, _ = await self.run_trace(
                    objective, [self.read(i) for i in range(16)], True)
                self.assertEqual(budget.lease_renewals, 0)
                self.assertLess(model.requests, 16)
                self.assertNotEqual(final.status, TaskStatus.RUNNING)

    async def test_affirmative_whole_repository_permits_distinct_reads(self):
        _, _, _, model, events = await self.run_trace(
            "Inspect the whole repository; do not edit any files.",
            [self.read(i) for i in range(16)])
        self.assertEqual(model.requests, 17)
        self.assertEqual(sum(e.kind is EventKind.ACTION_COMPLETED for e in events), 16)

    async def test_parent_or_whole_scope_does_not_renew_for_excluded_directory(self):
        directory = self.root / "src/unrelated"
        directory.mkdir()
        for i in range(16):
            (directory / f"unit_{i}.py").write_text(f"VALUE = {i}\n")
        calls = [("read", {"operation": "file", "path": f"src/unrelated/unit_{i}.py"}) for i in range(16)]
        for objective in (
            "Explain src/; do not inspect src/unrelated/.",
            "Inspect the whole repository; do not inspect src/unrelated/.",
        ):
            with self.subTest(objective=objective):
                _, _, budget, model, _ = await self.run_trace(objective, calls, True)
                self.assertEqual(budget.lease_renewals, 0)
                self.assertLess(model.requests, 16)

    async def test_repeated_same_read_with_prose_stops_with_paired_results(self):
        task, final, budget, model, events = await self.run_trace(
            "解释 src/unit_0.py，只读", [self.read(0)] * 12, True)
        self.assertLessEqual(model.requests, 4)
        self.assertEqual(budget.lease_renewals, 0)
        self.assertEqual(final.status, TaskStatus.PAUSED)
        requested = sum(e.kind is EventKind.ACTION_REQUESTED for e in events)
        completed = sum(e.kind is EventKind.ACTION_COMPLETED for e in events)
        self.assertEqual(requested, completed)
        self.assertEqual(len([m for m in await self.app.sessions.load_messages(task.thread_id)
                              if m.role == "tool"]), completed)

    async def test_real_edits_without_prose_use_generation_and_invalidate_subject(self):
        calls = [("write", {"operation": "file", "path": f"src/unit_{i}.md",
                             "content": f"Value {i + 100}\n"}) for i in range(7)]
        task, final, _, model, events = await self.run_trace("修改 src/ 内各 Markdown 说明文档数值", calls)
        self.assertEqual(model.requests, 8)
        state = await self.app.sessions.load_task_state(task.thread_id)
        self.assertGreaterEqual(state.code_generation, 7)
        self.assertEqual((self.root / "src/unit_6.md").read_text(), "Value 106\n")
        self.assertFalse(any(e.kind is EventKind.TASK_BUDGET_WARNING and
            e.payload.get("category") == "exploration" for e in events))
        self.assertNotEqual(final.status, TaskStatus.RUNNING)

    async def test_failed_reads_do_not_renew_or_count_as_information(self):
        calls = [("read", {"operation": "file", "path": f"missing-{i}.py"}) for i in range(24)]
        _, final, budget, model, events = await self.run_trace("解释 missing-0.py，只读", calls, True)
        self.assertEqual(budget.lease_renewals, 0)
        self.assertLess(model.requests, 24)
        self.assertTrue(all(e.payload["result"]["is_error"] for e in events
                            if e.kind is EventKind.ACTION_COMPLETED))
        self.assertNotEqual(final.status, TaskStatus.RUNNING)
