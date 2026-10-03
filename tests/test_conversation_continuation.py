from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import Message, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.controller import AgentController
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workflows.service import WorkflowService
from chaos_agent.foreground_tasks import IntegratedForegroundTaskController


class _OfflineEngine:
    async def run(self, *_args, **_kwargs):
        raise AssertionError("conversation setup must not invoke a model")
        yield


class ConversationContinuationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.sessions = SQLiteSessionRepository(self.root / "sessions.sqlite3")
        self.addCleanup(self.sessions.close)
        self.controller = IntegratedForegroundTaskController(
            AgentController(_OfflineEngine()), self.sessions, self.root,
            subagents=object(), workflows=WorkflowService(self.sessions),
            task_mode_supplier=lambda: "ask",
        )

    async def _completed_thread(self, root=None):
        thread = await self.sessions.create_thread("Original conversation")
        task = await self.sessions.create_task(
            thread, TaskContract("Old objective", TaskAuthorization.local_workspace(str(root or self.root)))
        )
        await self.sessions.append_message(thread, Message("user", "Original question"))
        await self.sessions.append_message(thread, Message("assistant", "Original answer"))
        await self.sessions.append_event(thread, AgentEvent(EventKind.COMPLETED))
        await self.sessions.transition_task(task.id, TaskStatus.RUNNING)
        await self.sessions.transition_task(task.id, TaskStatus.COMPLETED)
        return thread, task

    async def test_empty_selected_thread_is_used_for_first_task(self):
        thread = await self.sessions.create_thread()
        task = await self.controller.start("Explain the module", thread_id=thread)
        self.assertEqual(task.thread_id, thread)
        self.assertEqual((await self.sessions.list_threads())[0].id, thread)
        self.assertEqual(task.contract.objective, "Explain the module")
        self.assertFalse(task.contract.authorization.allow_workspace_write)
        self.assertIsNotNone(await self.sessions.load_workflow_for_task(task.id))

    async def test_completed_continuation_inherits_messages_without_reopening_old_task(self):
        source, old = await self._completed_thread()
        await self.sessions.get_or_create_task_budget(source, "old-model", EngineLimits())
        await self.sessions.consume_task_usage(old.id, Usage(input_tokens=23, output_tokens=17))
        old_summary = (await self.sessions.list_threads())[0]
        task = await self.controller.start("Explain the next step", source_thread_id=source)
        self.assertNotEqual(task.thread_id, source)
        self.assertEqual(task.contract.objective, "Explain the next step")
        self.assertEqual(task.status, TaskStatus.CREATED)
        self.assertFalse(task.contract.authorization.allow_workspace_write)
        self.assertEqual(await self.sessions.load_messages(task.thread_id), await self.sessions.load_messages(source))
        self.assertEqual((await self.sessions.load_thread_relation(task.thread_id)).parent_thread_id, source)
        self.assertEqual((await self.sessions.load_task(old.id)).status, TaskStatus.COMPLETED)
        preserved = next(item for item in await self.sessions.list_threads() if item.id == source)
        self.assertEqual(preserved, old_summary)
        self.assertEqual(await self.sessions.load_events(task.thread_id), ())
        budget = await self.sessions.load_task_budget(task.id)
        self.assertEqual(budget.input_tokens + budget.output_tokens, 0)
        old_budget = await self.sessions.load_task_budget(old.id)
        self.assertEqual(old_budget.input_tokens + old_budget.output_tokens, 40)

    async def test_legacy_history_can_continue_as_a_new_task(self):
        source = await self.sessions.create_thread("Legacy conversation")
        await self.sessions.append_message(source, Message("user", "Earlier question"))
        task = await self.controller.start("Follow up", source_thread_id=source)
        self.assertEqual((await self.sessions.load_messages(task.thread_id))[0].content, "Earlier question")
        self.assertIsNone(await self.sessions.load_task_for_thread(source))

    async def test_nonterminal_source_requires_original_recovery_path(self):
        original = await self.controller.start("First objective")
        count = len(await self.sessions.list_threads())
        with self.assertRaisesRegex(RuntimeError, "resume the existing task"):
            await self.controller.start("Follow up", source_thread_id=original.thread_id)
        self.assertEqual(len(await self.sessions.list_threads()), count)

    async def test_existing_task_and_nonempty_thread_cannot_be_repurposed(self):
        source, _old = await self._completed_thread()
        with self.assertRaisesRegex(RuntimeError, "only an empty conversation"):
            await self.controller.start("Follow up", thread_id=source)
        legacy = await self.sessions.create_thread()
        await self.sessions.append_message(legacy, Message("user", "History"))
        with self.assertRaisesRegex(RuntimeError, "only an empty conversation"):
            await self.controller.start("Follow up", thread_id=legacy)
        with self.assertRaises(ValueError):
            await self.controller.start("Follow up", thread_id=source, source_thread_id=source)

    async def test_foreign_project_cannot_continue_in_current_workspace(self):
        source, _old = await self._completed_thread(self.root / "another-project")
        with self.assertRaisesRegex(RuntimeError, "another project"):
            await self.controller.start("Follow up", source_thread_id=source)
        self.assertEqual(len(await self.sessions.list_threads()), 1)

    async def test_saved_runtime_is_restored_before_new_contract_is_frozen(self):
        source, old = await self._completed_thread()
        current = ["current-profile"]

        async def restore(task_id):
            self.assertEqual(task_id, old.id)
            current[0] = "saved-profile"

        self.controller.restore_runtime_settings = AsyncMock(side_effect=restore)
        self.controller._profile_supplier = lambda: (current[0], "test-model", "responses", "example.test")
        task = await self.controller.start("Follow up", source_thread_id=source)
        self.assertEqual(task.contract.profile_id, "saved-profile")
        self.controller.restore_runtime_settings.assert_awaited_once_with(old.id)

    async def test_busy_workspace_rejects_continuation_before_switching_runtime(self):
        source, _old = await self._completed_thread()
        await self.controller.start("Another active objective")
        restore = AsyncMock()
        self.controller.restore_runtime_settings = restore
        with self.assertRaisesRegex(RuntimeError, "already active"):
            await self.controller.start("Follow up", source_thread_id=source)
        restore.assert_not_awaited()
        self.assertEqual(len(await self.sessions.list_threads()), 2)

    async def test_workspace_setup_failure_does_not_leave_a_copied_conversation(self):
        source, _old = await self._completed_thread()
        with patch("chaos_agent.foreground_tasks.prepare_workspace", new=AsyncMock(side_effect=RuntimeError("workspace unavailable"))):
            with self.assertRaisesRegex(RuntimeError, "workspace unavailable"):
                await self.controller.start("Follow up", source_thread_id=source)
        self.assertEqual(len(await self.sessions.list_threads()), 1)

    async def test_invalid_contract_does_not_leave_a_copied_conversation(self):
        source, _old = await self._completed_thread()
        with self.assertRaises(ValueError):
            await self.controller.start("x" * 1025, source_thread_id=source)
        self.assertEqual(len(await self.sessions.list_threads()), 1)


if __name__ == "__main__":
    unittest.main()
