import json
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from dataclasses import replace
from pathlib import Path

from chaos_agent.project_memory import ProjectMemoryControl, project_identity
from chaos_agent.project_memory_context import ProjectMemoryContextBuilder
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import Message
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.task_state import TaskState
from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.sessions.workspace_models import WorkspaceLineageRecord
from code_agent.context.tokens import estimate_tokens


class ProjectMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.a, self.b = self.root / "a", self.root / "b"
        self.a.mkdir(); self.b.mkdir()
        path = self.root / "test.sqlite3"
        self.assertTrue(path.resolve().is_relative_to(self.root))
        self.repo = SQLiteSessionRepository(path)
        self.control = ProjectMemoryControl(self.repo, self.a)
        self.thread = await self.repo.create_thread()
        self.control.bind_thread(self.thread)

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def request(self, query="SQLite"):
        return ContextRequest(self.thread, 1, (), query, (), TaskState.empty(), CancellationToken())

    async def test_scoped_crud_cas_forget_and_reopen(self):
        record = await self.control.save("SQLite preferred", thread_id=self.thread,
            source_refs={"entry_origin": "forged", "file": "not opened"})
        self.assertEqual(record.source_refs["entry_origin"], "user_explicit")
        other = ProjectMemoryControl(self.repo, self.b)
        with self.assertRaises(SessionNotFound):
            await other.show(record.memory_id)
        with self.assertRaises(SessionNotFound):
            await other.revise(record.memory_id, "overwrite", expected_revision=1)
        revised = await self.control.revise(record.memory_id, "SQLite WAL preferred", expected_revision=1)
        with self.assertRaises(ValueError):
            await self.control.revise(record.memory_id, "stale", expected_revision=1)
        self.repo.close()
        self.repo = SQLiteSessionRepository(self.root / "test.sqlite3")
        self.control = ProjectMemoryControl(self.repo, self.a)
        self.assertEqual((await self.control.search("SQLite"))[0].revision, 2)
        await self.control.withdraw(record.memory_id, revision=revised.revision)
        self.assertEqual(await self.control.search("SQLite"), ())
        await self.control.delete(record.memory_id)
        with self.assertRaises(ValueError):
            await self.control.save("SQLite WAL preferred")

    async def test_foreign_task_parent_and_checkpoint_cannot_authorize(self):
        foreign = await self.repo.create_thread()
        await self.repo.create_task(foreign, TaskContract("read", TaskAuthorization.local_workspace(str(self.b))))
        child = await self.repo.create_thread(parent_thread_id=foreign)
        for thread in (foreign, child):
            with self.assertRaises(PermissionError):
                await self.control.save("foreign", thread_id=thread)
        unbound = await self.repo.create_thread()
        await self.repo.create_checkpoint(unbound, "remote-session", {"source_root": str(self.a)})
        with self.assertRaises(PermissionError):
            await self.control.project_id_for(unbound)

    async def test_projection_latest_user_conditions_tokens_and_capabilities(self):
        await self.repo.append_message(self.thread, Message("user", "SQLite"))
        await self.control.save("SQLite valid")
        await self.control.save("SQLite unknown", conditions={"branch": "missing"})
        await self.control.save("SQLite wrong", conditions={"source_root": str(self.b)})
        await self.control.save("SQLite " + "x" * 7000)
        class Inner:
            marker = object()
            async def build(self, request):
                return request
            async def compact_context(self, *args):
                return self.marker
        inner = Inner()
        wrapper = ProjectMemoryContextBuilder(inner, self.control)
        result = await wrapper.build(self.request(""))
        payload = json.loads(result.project_memory)
        self.assertEqual([item["content"] for item in payload], ["SQLite valid"])
        self.assertLessEqual(estimate_tokens(result.project_memory), 1024)
        self.assertEqual(result.task_facts, {})
        self.assertIs(await wrapper.compact_context(), inner.marker)
        with self.assertRaises(TypeError):
            replace(result, project_memory=None)
        with self.assertRaises(ValueError):
            replace(result, project_memory="中" * 6000)

    async def test_linked_git_tree_identity_is_same_distinct_workspace_is_not(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.a), *args], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git("init")
        (self.a / "file").write_text("test")
        git("add", "file")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", "test")
        linked = self.root / "linked"
        git("worktree", "add", "--detach", str(linked))
        self.assertEqual(project_identity(linked), project_identity(self.a))
        self.assertNotEqual(project_identity(self.a), project_identity(self.b))
        git("worktree", "remove", str(linked))

    async def test_managed_lineage_and_fork_share_authorized_source(self):
        task = await self.repo.create_task(self.thread,
            TaskContract("inspect", TaskAuthorization.local_workspace(str(self.b))))
        await self.repo.create_lineage(WorkspaceLineageRecord(
            uuid.uuid4().hex, repository_id="a" * 64, source_root=str(self.a),
            worktree_root=str(self.b), branch_name="managed", head_commit="a" * 40,
            owner_task_id=task.id))
        child = await self.repo.create_thread(parent_thread_id=self.thread)
        saved = await self.control.save("SQLite same source", thread_id=child)
        self.assertEqual(saved.scope_id, await self.control.project_id_for(self.thread))
        self.assertEqual((await self.control.search("SQLite", thread_id=child))[0], saved)

    async def test_applicability_is_current_scoped_and_separate_from_lifecycle(self):
        items = [await self.control.save("SQLite " + str(index), conditions=conditions)
                 for index, conditions in enumerate(({}, {"branch": "main"},
                     {"source_root": str(self.b)}, {"source_root": str(self.a)}))]
        self.assertEqual([await self.control.applicability(item.memory_id) for item in items],
                         ["applicable", "needs_check", "conflict", "applicable"])
        await self.control.withdraw(items[0].memory_id, revision=1)
        self.assertEqual(await self.control.applicability(items[0].memory_id), "withdrawn")
        with self.assertRaises(SessionNotFound):
            await ProjectMemoryControl(self.repo, self.b).applicability(items[0].memory_id)

    async def test_latest_user_query_does_not_reload_or_pair_large_tool_tail(self):
        await self.repo.append_message(self.thread, Message("user", "SQLite"))
        await self.control.save("SQLite reference")
        class Inner:
            async def build(self, request):
                return request
        wrapper = ProjectMemoryContextBuilder(Inner(), self.control)
        request = replace(self.request(""), messages=(Message("user", "SQLite"),
            Message("tool", "x" * (1024 * 1024 + 1), tool_call_id="large-call")))
        with patch.object(self.repo, "load_context_messages", new=AsyncMock(
                side_effect=AssertionError("must not materialize tool groups"))):
            with patch.object(self.repo, "read_history_page", wraps=self.repo.read_history_page) as read:
                direct = await wrapper.build(request)
                self.assertIn("SQLite reference", direct.project_memory)
                read.assert_not_called()
                fallback = await wrapper.build(self.request(""))
                self.assertIn("SQLite reference", fallback.project_memory)
                read.assert_awaited_once_with(self.thread, role="user", newest=True,
                    limit=1, max_bytes=1024 * 1024)

    async def test_optional_projection_cannot_veto_foreign_authorized_task(self):
        foreign = await self.repo.create_thread()
        await self.repo.create_task(foreign,
            TaskContract("valid foreign task", TaskAuthorization.local_workspace(str(self.b))))
        await self._assert_projection_permission_drops_only_references(foreign)

    async def test_optional_projection_cannot_mask_inner_rules_on_unbound_thread(self):
        unbound = await self.repo.create_thread()
        await self._assert_projection_permission_drops_only_references(unbound)

    async def _assert_projection_permission_drops_only_references(self, thread):
        class Inner:
            async def build(self, request):
                return request
        wrapper = ProjectMemoryContextBuilder(Inner(), self.control)
        request = replace(self.request(), thread_id=thread, project_memory="forged references")
        with patch.object(self.control, "search", new=AsyncMock(
                side_effect=AssertionError("foreign scope must not query"))) as search:
            result = await wrapper.build(request)
            self.assertEqual(result.project_memory, "")
            self.assertEqual(result.thread_id, thread)
            self.assertEqual(result.messages, request.messages)
            self.assertIs(result.cancellation, request.cancellation)
            search.assert_not_awaited()
        with self.assertRaises(PermissionError):
            await self.control.save("still forbidden", thread_id=thread)
