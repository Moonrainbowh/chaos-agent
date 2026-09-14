import asyncio
from pathlib import Path

from code_agent.sessions.models import MemoryLifecycle
from code_agent.sessions._memory import assess_memory_applicability
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.models import Message, ToolCall


def test_project_memory_is_scoped_versioned_and_idempotent(tmp_path: Path) -> None:
    async def run() -> None:
        repo = SQLiteSessionRepository(tmp_path / "sessions.db")
        first = await repo.create_memory("project", "alpha", "decision", "use SQLite", lifecycle=MemoryLifecycle.ACTIVE, source_refs={"message": 3}, idempotency_key="decision-1")
        duplicate = await repo.create_memory("project", "alpha", "decision", "different", lifecycle=MemoryLifecycle.ACTIVE, idempotency_key="decision-1")
        assert duplicate == first
        revised = await repo.revise_memory(first.memory_id, "use SQLite WAL", expected_revision=1, lifecycle=MemoryLifecycle.ACTIVE)
        assert revised.revision == 2
        same_content = await repo.create_memory("project", "alpha", "decision", "use SQLite WAL", lifecycle=MemoryLifecycle.ACTIVE)
        assert same_content == revised
        try:
            await repo.revise_memory(first.memory_id, "stale", expected_revision=1)
        except ValueError as error:
            assert "conflict" in str(error)
        else:
            raise AssertionError("stale revision was accepted")
        try:
            await repo.set_memory_lifecycle(first.memory_id, revision=1, lifecycle=MemoryLifecycle.ARCHIVED)
        except ValueError as error:
            assert "conflict" in str(error)
        else:
            raise AssertionError("stale lifecycle revision was accepted")
        assert [item.content for item in await repo.list_memories("project", "alpha")] == ["use SQLite WAL"]
        assert len(await repo.list_memories("project", "alpha", include_history=True)) == 2
        assert await repo.list_memories("project", "beta") == ()
        await repo.set_memory_lifecycle(first.memory_id, revision=2, lifecycle=MemoryLifecycle.WITHDRAWN)
        assert await repo.list_memories("project", "alpha") == ()
        await repo.delete_memory(first.memory_id)
        assert await repo.list_memories("project", "alpha", include_history=True) == ()
        try:
            await repo.create_memory("project", "alpha", "decision", "use SQLite WAL", lifecycle=MemoryLifecycle.ACTIVE)
        except ValueError as error:
            assert "forgotten" in str(error)
        else:
            raise AssertionError("forgotten memory was recreated")

        try:
            await repo.create_memory("user", "local", "preference", "compact output", lifecycle=MemoryLifecycle.ACTIVE)
        except PermissionError:
            pass
        else:
            raise AssertionError("user scope must require explicit authorization")
        global_memory = await repo.create_memory("user", "local", "preference", "compact output", lifecycle=MemoryLifecycle.ACTIVE, allow_user_scope=True)
        assert global_memory.scope_type == "user"
        assert await repo.search_memories("alpha", "SQLite") == ()  # forgotten project fact is excluded
        assert [m.content for m in await repo.search_memories("other", "compact", user_scope_id="local", allow_user_scope=True)] == ["compact output"]
        diagnostics = await repo.search_memory_diagnostics("other", "compact", user_scope_id="local", allow_user_scope=True)
        assert diagnostics["allowed_scopes"] == ("project:other", "user:local")
        assert diagnostics["selected"] == (global_memory.memory_id + "@1",)
        assert diagnostics["excluded"]["scope"] == 0
        assert assess_memory_applicability(global_memory, {}) == "needs_check"
        conditional = await repo.create_memory("project", "alpha", "constraint", "only on branch main", lifecycle=MemoryLifecycle.ACTIVE, conditions={"branch": "main"})
        assert assess_memory_applicability(conditional, {"branch": "main"}) == "applicable"
        assert assess_memory_applicability(conditional, {"branch": "dev"}) == "conflict"
        promoted = await repo.promote_memory(conditional.memory_id, "local", "Prefer explicit branch conditions", allow_user_scope=True, lifecycle=MemoryLifecycle.ACTIVE)
        assert promoted.scope_type == "user"
        assert promoted.derived_from == conditional.memory_id

        thread = await repo.create_thread()
        task = await repo.create_task(thread, TaskContract("resume safely", TaskAuthorization.local_workspace(str(tmp_path))))
        await repo.create_checkpoint(thread, "pause", {"reason": "user"})
        await repo.write_context_note(thread, "note-1", "handoff.md", "next: inspect")
        await repo.append_message(thread, Message("assistant", tool_calls=(ToolCall("call-1", "write_file", {"path": "x"}),)))
        checklist = await repo.recovery_checklist(task.id)
        assert checklist["task_id"] == task.id
        assert checklist["thread_id"] == thread
        assert checklist["latest_checkpoint"]["label"] == "pause"
        assert checklist["notes"] == ({"path": "handoff.md", "revision": 1},)
        assert checklist["notes_lagging"] is True
        assert checklist["notes_coverage"]["message_sequence"] == 0
        assert checklist["unresolved_tool_calls"] == ({"tool_call_id": "call-1", "tool_name": "write_file", "status": "unknown"},)

    asyncio.run(run())
