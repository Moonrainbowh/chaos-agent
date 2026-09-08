import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ActionRequest, ContextBundle, Message, ToolCall
from code_agent.core.task_state import TaskState
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.persistent_builder import PersistentContextBuilder
from code_agent.context_windows.persistent_tools import PersistentToolService
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
from code_agent.sessions.repository import SQLiteSessionRepository


class Prefix:
    async def build(self, request):
        return ContextBundle("Trusted rules", request.messages)


class NoSummary:
    async def write(self, *args):
        raise AssertionError("persistent strategy must never summarize")


class PersistentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sessions.db"
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.policy = WindowPolicy(strategy="persistent", work_tokens=10000, safety_tokens=100)
        self.builder = self.make_builder()
        self.service = PersistentToolService(self.repo, lambda: self.thread, self.builder)
        self.revision = 0
        await self.repo.append_message(self.thread, Message("user", "Keep the API contract; repair the bug."))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def make_builder(self):
        return PersistentContextBuilder(Prefix(), self.repo, self.policy,
                                        ApiContextLimits(20000, 1000), PromptTokenCounter(), NoSummary())

    async def build(self):
        self.revision += 1
        return await self.builder.build(ContextRequest(self.thread, self.revision, (), "", (),
                                                       TaskState(), CancellationToken()))

    async def tool(self, name, args, key="call", persist=False):
        request = ActionRequest(key, name, args)
        if persist:
            await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall(key, name, args),)))
        result = await self.service.dispatch(request, CancellationToken())
        if persist:
            await self.repo.append_message(self.thread, Message("tool", str(result.output), tool_call_id=key))
        self.assertFalse(result.is_error, str(result.output))
        return result.output

    async def test_three_windows_restart_and_exact_tool_history(self):
        await self.tool("notes_write_file", {"path": "state.md", "text": "private checkpoint"})
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(
            ToolCall("read", "read_file", {"path": "hidden-in-arguments.py"}),)))
        await self.repo.append_message(self.thread, Message("tool", "old evidence 原文", tool_call_id="read"))
        before = await self.repo.load_messages(self.thread)
        first = await self.build()
        for i in range(2):
            await self.tool("new_context", {}, f"reset{i}", persist=True)
            bundle = await self.build()
            self.assertEqual(bundle.measurements["window_number"], i + 1)
            self.assertNotIn("old evidence", str(bundle.messages))
            self.assertNotIn("private checkpoint", bundle.system_prompt + str(bundle.messages))
            self.assertIn("Keep the API contract", str(bundle.messages))
        self.repo = SQLiteSessionRepository(self.path)
        self.builder = self.make_builder()
        self.service = PersistentToolService(self.repo, lambda: self.thread, self.builder)
        restored = await self.build()
        self.assertEqual(restored.messages, bundle.messages)
        self.assertEqual((await self.repo.load_messages(self.thread))[:len(before)], before)
        windows = await self.tool("history_list_windows", {})
        self.assertEqual(len(windows["items"]), 3)
        search = await self.tool("history_search_contents", {"query": "hidden-in-arguments.py"})
        hit = search["items"][0]
        item = await self.tool("history_read_item", {"item_id": hit["item_id"], "window_id": hit["window_id"]})
        self.assertIn("hidden-in-arguments.py", item["text"])
        self.assertIn(hit["window_id"], first.system_prompt)
        self.assertEqual((await self.repo.context_records(self.thread, "usage")), ())

    async def test_soft_threshold_warns_without_forcing_rotation(self):
        await self.repo.append_message(self.thread, Message("assistant", "e" * 7600))
        bundle = await self.build()
        self.assertGreater(bundle.measurements["prompt_tokens"], 8750)
        self.assertEqual(bundle.measurements["window_number"], 0)
        self.assertIn("Save notes and call new_context", bundle.system_prompt)
        status = await self.tool("get_context_remaining", {})
        self.assertLessEqual(status["context_tokens_remaining"], 10000 - bundle.measurements["prompt_tokens"])

    async def test_capacity_fallback_preserves_all_original_content(self):
        await self.repo.append_message(self.thread, Message("assistant", "x" * 11000))
        before = await self.repo.load_messages(self.thread)
        bundle = await self.build()
        self.assertEqual(bundle.measurements["window_number"], 1)
        self.assertIn("Capacity forced a reset", bundle.system_prompt)
        self.assertEqual(before, await self.repo.load_messages(self.thread))
        self.assertEqual((await self.repo.context_records(self.thread, "window"))[0]["carry"], "")

    async def test_pending_tool_group_cannot_be_discarded(self):
        await self.tool("new_context", {})
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall("pending", "read_file", {}),)))
        with self.assertRaisesRegex(ValueError, "unfinished"):
            await self.build()
        self.assertEqual(await self.repo.context_records(self.thread, "window"), ())

    async def test_single_oversized_user_request_fails_without_reset(self):
        await self.repo.append_message(self.thread, Message("user", "x" * 11000))
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            await self.build()
        self.assertEqual(await self.repo.context_records(self.thread, "window"), ())

    async def test_note_replace_append_retry_concurrency_and_task_isolation(self):
        await self.tool("notes_write_file", {"path": "work/state.md", "text": "A"}, "n1")
        await self.tool("notes_append_to_file", {"path": "work/state.md", "text": "B"}, "n2")
        await self.tool("notes_append_to_file", {"path": "work/state.md", "text": "B"}, "n2")
        note = await self.tool("notes_read_file", {"path": "work/state.md"})
        self.assertEqual(note["text"], "AB")
        await self.tool("notes_write_file", {"path": "work/state.md", "text": "new"}, "n3")
        await asyncio.gather(*(self.repo.write_context_note(self.thread, str(i), "work/state.md", str(i), append=True)
                               for i in range(4)))
        note = await self.tool("notes_read_file", {"path": "work/state.md"})
        self.assertEqual(set(note["text"][3:]), set("0123"))
        other = await self.repo.create_thread()
        self.assertEqual(await self.repo.context_note_files(other), ())
        self.assertEqual(await self.repo.list_checkpoints(self.thread), ())
        for path in ("../state", "/other/notes/state", "C:/secret", "a//b", "~user", "a\\b"):
            with self.assertRaises(ValueError):
                await self.repo.write_context_note(self.thread, path, path, "bad")

    async def test_history_pagination_and_unknown_references(self):
        await self.repo.append_message(self.thread, Message("assistant", "长工具文本" * 2000))
        hit = (await self.tool("history_search_contents", {"query": "长工具文本"}))["items"][0]
        args = {"item_id": hit["item_id"], "max_chars": 997}
        parts = []
        while True:
            result = await self.tool("history_read_item", args)
            parts.append(result["text"])
            if result["next_offset"] is None:
                break
            args["offset"] = result["next_offset"]
        import json
        self.assertEqual(json.loads("".join(parts))["content"], "长工具文本" * 2000)
        empty = await self.tool("history_list_items", {"window_id": "unknown"})
        self.assertFalse(empty["items"])
        other = await self.repo.create_thread()
        foreign = PersistentToolService(self.repo, lambda: other, SimpleNamespace())
        result = await foreign.dispatch(ActionRequest("r", "history_read_item", {"item_id": hit["item_id"]}), CancellationToken())
        self.assertTrue(result.is_error)

    async def test_invalid_tool_arguments_and_remaining_status_are_isolated(self):
        await self.build()
        other = await self.repo.create_thread()
        foreign = PersistentToolService(self.repo, lambda: other, self.builder)
        status = await foreign.dispatch(ActionRequest("remaining", "get_context_remaining", {}), CancellationToken())
        self.assertIsNone(status.output["context_tokens_remaining"])
        for name, args in (
            ("notes_write_file", {"path": "state.md", "text": "bad", "thread_id": other}),
            ("history_list_items", {"limit": True}),
            ("history_read_item", {"item_id": "unknown", "max_chars": 0}),
            ("notes_write_file", {"path": "state.md"}),
        ):
            result = await self.service.dispatch(ActionRequest("bad", name, args), CancellationToken())
            self.assertTrue(result.is_error)
        self.assertEqual(await self.repo.context_note_files(self.thread), ())
