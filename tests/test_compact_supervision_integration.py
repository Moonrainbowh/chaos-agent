"""Offline model traces through the default production dispatcher and journal."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.core.events import EventKind
from code_agent.core.exploration_repeat import ExplorationRepeatObserver, ToolOnlyConvergenceGuard
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall
from code_agent.core.action_semantics import operation_kind, resolve_supervision_call
from code_agent.core.models import ActionRequest, ToolDefinition
from code_agent.core.cancellation import CancellationToken
from code_agent.core.tests._engine_support import FakeContextBuilder, FakeModelClient
from tests.test_tui_repair_integration import application_fixture
from chaos_agent.restricted_dispatcher import RestrictedDispatcher
from chaos_agent.tools import tool_definitions


def stream(call):
    return [ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED)]


class CompactSupervisionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def run_trace(self, calls, files=(), *, managed_task=False):
        observed, turns = [], []
        real_observe, real_turn = ExplorationRepeatObserver.observe, ToolOnlyConvergenceGuard.observe
        def observe(observer, call, result):
            observed.append(call)
            return real_observe(observer, call, result)
        def observe_turn(guard, **kwargs):
            turns.extend(kwargs["calls"])
            return real_turn(guard, **kwargs)
        with application_fixture() as app:
            try:
                root = app.dispatcher.editor.guard.root
                for name, content in files:
                    (root / name).write_text(content, encoding="utf-8")
                if callable(calls):
                    calls = calls(app.dispatcher._source.repo_index)
                model = FakeModelClient([*[stream(call) for call in calls],
                    [ModelEvent(ModelEventKind.TEXT_DELTA, text="done"), ModelEvent(ModelEventKind.COMPLETED)]])
                engine = app.controller._engine
                engine._model, engine._context = model, FakeContextBuilder()
                with patch.object(ExplorationRepeatObserver, "observe", observe), \
                     patch.object(ToolOnlyConvergenceGuard, "observe", observe_turn):
                    if managed_task:
                        task = await app.tasks.start("Update the .txt documentation files")
                        events = [event async for event in app.tasks.events(task.id)]
                        thread = task.thread_id
                    else:
                        events = [event async for event in engine.run("Update the files")]
                        thread = events[0].payload["thread_id"]
                messages = await app.sessions.load_messages(thread)
                output = {p.name: p.read_text(encoding="utf-8") for p in root.glob("*.txt")}
                return events, messages, observed, turns, output
            finally:
                await app.aclose()

    async def test_five_writes_and_five_edits_match_legacy_without_early_stop(self):
        outcomes = []
        for compact in (False, True):
            calls = []
            for index in range(5):
                args = {"path": f"file-{index}.txt", "content": "old"}
                calls.append(ToolCall(f"write-{index}", "write" if compact else "write_file",
                    {"operation": "file", **args} if compact else args))
            for index in range(5):
                args = {"path": f"file-{index}.txt", "old_text": "old", "new_text": "new"}
                calls.append(ToolCall(f"edit-{index}", "edit" if compact else "replace_text",
                    {"operation": "replace", **args} if compact else args))
            events, messages, observed, turns, output = await self.run_trace(calls, managed_task=True)
            results = [e.payload["result"] for e in events if e.kind is EventKind.ACTION_COMPLETED]
            self.assertEqual(len(results), 10)
            self.assertTrue(all(not r["is_error"] for r in results))
            self.assertFalse(any(e.kind is EventKind.TASK_BUDGET_WARNING for e in events))
            self.assertEqual([c.name for c in observed], ["write_file"] * 5 + ["replace_text"] * 5)
            self.assertEqual(observed, turns)
            self.assertEqual([r["name"] for r in results], [c.name for c in calls])
            self.assertEqual([m.tool_call_id for m in messages if m.role == "tool"], [c.id for c in calls])
            outcomes.append(output)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(set(outcomes[0].values()), {"new"})

    async def test_repeated_reads_and_changed_content_are_observed_equally(self):
        for compact in (False, True):
            def read(index):
                args = {"path": "same.txt"}
                return ToolCall(str(index), "read" if compact else "read_file",
                    {"operation": "file", **args} if compact else args)
            write = ToolCall("write", "write", {"operation": "file", "path": "same.txt", "content": "new"})
            events, _, observed, _, _ = await self.run_trace(
                [read(1), write, read(2), read(3), read(4)], [("same.txt", "old")])
            results = [e.payload["result"] for e in events if e.kind is EventKind.ACTION_COMPLETED]
            self.assertTrue(all(not r["is_error"] for r in results))
            warnings = [e.payload["count"] for e in events
                if e.kind is EventKind.TASK_BUDGET_WARNING and e.payload.get("category") == "exact_repeat"]
            self.assertEqual(warnings, [2, 3])
            self.assertEqual([c.name for c in observed], ["read_file", "write_file", "read_file", "read_file", "read_file"])

    async def test_failed_call_cannot_be_retried_by_switching_alias(self):
        args = {"path": "missing.txt", "old_text": "absent", "new_text": "new"}
        calls = [ToolCall("one", "edit", {"operation": "replace", **args}),
                 ToolCall("two", "replace_text", args),
                 ToolCall("three", "edit", {"operation": "replace", **args})]
        events, messages, _, _, _ = await self.run_trace(calls)
        results = [e.payload["result"] for e in events if e.kind is EventKind.ACTION_COMPLETED]
        self.assertEqual([r["name"] for r in results], [c.name for c in calls])
        self.assertEqual([r["output"].get("error_code") for r in results[1:]],
                         ["duplicate_failed_call_blocked"] * 2)
        self.assertEqual(sum(e.kind is EventKind.ACTION_STARTED for e in events), 1)
        self.assertEqual([m.tool_call_id for m in messages if m.role == "tool"], [c.id for c in calls])

    async def test_default_slice_read_with_nested_parameters_and_output_is_observed(self):
        def calls(index):
            snapshot = index.snapshot_for_turn()
            entry = next(item for item in snapshot.entries if item.path == "same.txt")
            signature = entry.signature
            arguments = {"generation": snapshot.generation, "targets": [{
                "path": entry.path, "start_line": 1, "end_line": 1,
                "expected_size_bytes": signature.size_bytes,
                "expected_modified_ns": signature.modified_ns,
                "expected_device_id": signature.device_id,
                "expected_file_id": signature.file_id,
            }]}
            return [ToolCall(str(n), "read", {"operation": "slices", **arguments}) for n in range(3)]
        events, messages, observed, _, _ = await self.run_trace(calls, [("same.txt", "same")])
        results = [e.payload["result"] for e in events if e.kind is EventKind.ACTION_COMPLETED]
        self.assertEqual(len(results), 3)
        self.assertTrue(all(not r["is_error"] for r in results))
        self.assertEqual([r["output"]["slices"][0]["text"] for r in results], ["same\n"] * 3)
        self.assertEqual([c.name for c in observed], ["read_code_slices"] * 3)
        self.assertEqual([e.payload["count"] for e in events if e.kind is EventKind.TASK_BUDGET_WARNING
                          and e.payload.get("category") == "exact_repeat"], [2, 3])
        self.assertEqual([m.tool_call_id for m in messages if m.role == "tool"], ["0", "1", "2"])

    def test_execute_suboperations_keep_distinct_canonical_categories(self):
        definitions = tool_definitions(include_git=True)
        dispatcher = RestrictedDispatcher(SimpleNamespace(tools=lambda: definitions),
            [tool.name for tool in definitions], compact_tools=True)
        cases = (
            ("status", {}, "git_status", "read"),
            ("diff", {}, "git_diff", "read"),
            ("command", {"command": "echo x"}, "run_command", "other"),
            ("process", {"program": "python", "args": ["--version"]}, "run_process_v1", "other"),
            ("verify", {"kind": "python_unittest"}, "run_verification", "progress"),
        )
        for operation, arguments, expected, category in cases:
            with self.subTest(operation=operation):
                call = ToolCall(operation, "execute", {"operation": operation, **arguments})
                observed = resolve_supervision_call(call, dispatcher)
                self.assertEqual(observed.name, expected)
                self.assertEqual(operation_kind(observed), category)
                self.assertEqual(observed.id, call.id)

    async def test_plugin_resolution_does_not_replace_qualified_dispatch_or_mcp_identity(self):
        with application_fixture() as app:
            try:
                source = next(tool for tool in app.dispatcher.tools() if tool.name == "read_file")
                plugin = ToolDefinition("plugin.read", "plugin", source.parameters)
                app.dispatcher.plugins = SimpleNamespace(
                    definitions=lambda: (plugin,), targets=lambda: {"plugin.read": "read_file"})
                restricted = RestrictedDispatcher(app.dispatcher, ("plugin.read",), compact_tools=True)
                call = ToolCall("plugin-id", "plugin.read", {"path": "missing.txt"})
                observed = resolve_supervision_call(call, restricted)
                self.assertEqual((observed.name, observed.id), ("read_file", "plugin-id"))
                seen = []
                async def dispatch(request, *args, **kwargs):
                    seen.append(request)
                    from code_agent.core.models import ActionResult
                    return ActionResult(request.id, request.name, {"error": "policy denied"}, True)
                with patch.object(app.dispatcher, "dispatch", dispatch):
                    result = await restricted.dispatch(ActionRequest(call.id, call.name, call.arguments), CancellationToken())
                self.assertEqual(seen[0].name, "plugin.read")
                self.assertEqual((result.request_id, result.name), (call.id, call.name))
                mcp = ToolCall("mcp-id", "mcp.server.read", {"operation": "write_file"})
                self.assertEqual(resolve_supervision_call(mcp, restricted), mcp)
                self.assertEqual(operation_kind(mcp), "other")
            finally:
                await app.aclose()
