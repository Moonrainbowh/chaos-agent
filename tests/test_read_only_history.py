"""Public CLI history reads with execution dependencies deliberately unavailable."""
from __future__ import annotations

import asyncio
from contextlib import closing
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ToolCall
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.core.task_result import TaskResult
from code_agent.sessions.repository import SQLiteSessionRepository


class ReadOnlyHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "chaos-agent"
        self.state.mkdir()
        self.repo = SQLiteSessionRepository(self.state / "sessions.sqlite3")

    async def asyncTearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    async def task(self, title, status):
        thread = await self.repo.create_thread()
        task = await self.repo.create_task(thread, TaskContract(
            title, TaskAuthorization(str(self.root))))
        await self.repo.transition_task(task.id, TaskStatus.RUNNING)
        task = await self.repo.transition_task(task.id, status)
        return task

    def query(self, *arguments):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CHAOS_", "CODE_AGENT_"))}
        env.update(LOCALAPPDATA=str(self.root),
                   CHAOS_CONFIG=str(self.root / "unconfigured-provider.toml"),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")
                       + os.pathsep + str(Path(__file__).resolve().parents[1]))
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--probe", *arguments],
            cwd=self.root, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        return completed

    async def test_empty_list_does_not_import_execution_ui_or_plugins(self):
        result = self.query("task", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    async def test_populated_list_and_old_completed_result_keep_unknown_verification(self):
        task = await self.task("Old persisted task", TaskStatus.COMPLETED)
        listing = self.query("--profile", "missing", "task", "list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn(f"{task.id} completed Old persisted task", listing.stdout)
        result = self.query("task", "result", task.id)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["execution_status"], "completed")
        self.assertEqual(data["verification_status"], "unknown")

    async def test_saved_unverified_result_uses_shared_projection(self):
        task = await self.task("Not verified", TaskStatus.COMPLETED)
        state = await self.repo.load_task_state(task.thread_id)
        await self.repo.append_event(task.thread_id, AgentEvent(EventKind.TASK_RESULT, {
            "task_id": task.id, "task_updated_at": task.updated_at.isoformat(),
            "result_generation": state.code_generation,
            "result_subject_hash": state.subject_hash,
            "result": TaskResult("completed", "unchanged", "unverified",
                                 stop_code="completed").to_dict(),
        }))
        result = self.query("task", "result", task.id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["verification_status"], "unverified")

    async def test_cancelled_run_is_query_success_and_not_restored_as_paused(self):
        task = await self.task("Cancelled actual run", TaskStatus.PAUSED)
        state = await self.repo.load_task_state(task.thread_id)
        await self.repo.append_event(task.thread_id, AgentEvent(EventKind.TASK_STATUS_CHANGED, {
            "task_id": task.id, "status": "running", "run_instance_id": "attempt",
        }))
        await self.repo.append_event(task.thread_id, AgentEvent(EventKind.TASK_RESULT, {
            "task_id": task.id, "run_instance_id": "attempt",
            "result_generation": state.code_generation,
            "result_subject_hash": state.subject_hash,
            "result": TaskResult("cancelled", "unchanged", "unknown",
                                 stop_code="cancelled").to_dict(),
        }))
        result = self.query("task", "result", task.id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["execution_status"], "cancelled")
        self.assertEqual((await self.repo.load_task(task.id)).status, TaskStatus.PAUSED)

    async def test_recovery_reads_unresolved_action_without_resolving_or_starting(self):
        task = await self.task("Interrupted action", TaskStatus.INTERRUPTED)
        call = ToolCall("unknown-call", "execute_command", {"command": "secret"})
        await self.repo.append_message(task.thread_id, Message("assistant", tool_calls=(call,)))
        before = await self.repo.recovery_checklist(task.id)
        events = await self.repo.load_events(task.thread_id)
        result = self.query("task", "recovery", task.id)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["recovery_version"], before["recovery_version"])
        self.assertEqual(data["pending_action_records"][0]["tool_call_id"], call.id)
        self.assertEqual(await self.repo.recovery_checklist(task.id), before)
        self.assertEqual(await self.repo.load_events(task.thread_id), events)
        with closing(sqlite3.connect(self.state / "sessions.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_executions").fetchone()[0], 0)

    async def test_missing_task_and_corrupt_database_return_safe_nonzero(self):
        missing = self.query("task", "result", "private-task-key")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, "")
        self.assertIn("history error:", missing.stderr)
        self.assertNotIn("private-task-key", missing.stderr)
        (self.state / "sessions.sqlite3").write_text("private SQL corruption", encoding="utf-8")
        corrupt = self.query("task", "list")
        self.assertEqual(corrupt.returncode, 1)
        self.assertNotIn("private", corrupt.stderr)
        self.assertNotIn("SELECT", corrupt.stderr)

    async def test_injected_path_and_resolve_do_not_enter_readonly_service(self):
        from io import StringIO
        from chaos_agent.read_only_history import is_history_query, run_history_query
        output = StringIO()
        self.assertEqual(await run_history_query(("task", "list"), output.write,
            database_path=self.state / "sessions.sqlite3"), 0)
        self.assertFalse(is_history_query(("task", "resolve", "task")))
        self.assertFalse(is_history_query(("task", "resume", "task")))

    async def test_default_path_preserves_standard_legacy_database_migration(self):
        task = await self.task("Legacy saved task", TaskStatus.COMPLETED)
        self.repo.close()
        legacy = self.root / "code-agent"
        legacy.mkdir()
        original = self.state / "sessions.sqlite3"
        old_path = legacy / "sessions.sqlite3"
        original.rename(old_path)
        result = self.query("task", "result", task.id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["execution_status"], "completed")
        self.assertTrue(original.is_file())
        self.assertTrue(old_path.is_file())

    async def test_public_history_restores_persisted_messages_and_events_without_task(self):
        from code_agent.interfaces.history import load_thread_history
        thread = await self.repo.create_thread()
        await self.repo.append_message(thread, Message("user", "保存的中文问题"))
        await self.repo.append_message(thread, Message("assistant", "实际历史回答"))
        await self.repo.append_event(thread, AgentEvent(EventKind.RUN_STARTED, {"thread_id": thread}))
        await self.repo.append_event(thread, AgentEvent(EventKind.COMPLETED))
        await self.repo.create_goal(thread, "Persisted goal")
        await self.repo.create_checkpoint(thread, "saved", {"marker": "history"})
        expected = await load_thread_history(self.repo, thread)
        result = self.query("history", thread)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["thread_id"], thread)
        self.assertEqual(data["messages"], [message.to_dict() for message in expected.messages])
        self.assertEqual(data["events"], [event.to_dict() for event in expected.events])
        self.assertEqual(await load_thread_history(self.repo, thread), expected)
        self.assertEqual(await self.repo.list_tasks(include_terminal=True), ())

    async def test_history_missing_thread_and_unsupported_attachment_are_safe(self):
        missing = self.query("history", "private-thread-key")
        self.assertEqual(missing.returncode, 1)
        self.assertNotIn("private-thread-key", missing.stderr)
        attached = self.query("history", "thread", "--attach", "missing-file")
        self.assertEqual(attached.returncode, 2)
        self.assertIn("not supported", attached.stderr)
        help_result = self.query("history", "--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("history <thread-id>", help_result.stdout)


class _NoExecutionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("chaos_agent.app", "code_agent.providers", "code_agent.plugins",
                   "code_agent.interfaces.commands", "code_agent.interfaces.windows_tui",
                   "code_agent.runtime.local", "code_agent.runtime.platform",
                   "code_agent.runtime._powershell_runtime", "code_agent.runtime.posix")
        if any(fullname == item or fullname.startswith(item + ".") for item in blocked):
            raise ImportError("Execution dependency unavailable")
        return None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        sys.meta_path.insert(0, _NoExecutionImports())
        from chaos_agent.cli import run
        from chaos_agent.stdio import configure_windows_utf8_stdio
        configure_windows_utf8_stdio()
        raise SystemExit(asyncio.run(run(sys.argv[2:])))
    unittest.main()
