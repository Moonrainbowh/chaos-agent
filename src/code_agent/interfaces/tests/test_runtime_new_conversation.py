from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from code_agent.interfaces.runtime_picker import runtime_picker_items
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app


class RuntimeNewConversationTests(unittest.IsolatedAsyncioTestCase):
    def saved_app(self, *, active=True):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime)
        app.current_thread_id = "old-thread"
        app.active_task_id = "old-task" if active else None
        app.state.thread_id = "old-thread"
        app.state.task_id = "old-task"
        app.state.status = "waiting_decision" if active else "completed"
        app._append(DisplayKind.USER, "old conversation content")
        return app, runtime

    async def test_each_entrypoint_detaches_active_and_completed_conversations(self):
        for active in (True, False):
            for command, key, expected in (
                ("/model terra", "profile", "gpt56_terra"),
                (":effort high", "reasoning_effort", "high"),
                ("/mode model terra", "profile", "gpt56_terra"),
                ("/模式 模型 terra", "profile", "gpt56_terra"),
                ("/mode effort max", "reasoning_effort", "max"),
                ("/模式 思考 max", "reasoning_effort", "max"),
            ):
                with self.subTest(active=active, command=command):
                    app, runtime = self.saved_app(active=active)
                    old = app.state
                    self.assertTrue(await app.submit(command))
                    self.assertEqual(getattr(runtime.current, key), expected)
                    self.assertIsNone(app.current_thread_id)
                    self.assertIsNone(app.active_task_id)
                    self.assertIsNone(app.state.task_id)
                    self.assertIsNone(app.state.thread_id)
                    self.assertIsNot(app.state, old)
                    self.assertEqual(old.transcript, ["old conversation content"])
                    self.assertNotIn("old conversation content", app.state.transcript)
                    self.assertIn("New conversation", app.state.entries[0].text)

    async def test_same_selection_preserves_context_without_rebuilding(self):
        app, runtime = self.saved_app()
        old = app.state
        for command in ("/model sol", "/effort medium", "/mode model gpt56_sol", "/mode effort medium"):
            self.assertTrue(await app.submit(command))
            self.assertIs(app.state, old)
            self.assertEqual(app.current_thread_id, "old-thread")
            self.assertEqual(app.active_task_id, "old-task")
        self.assertEqual(runtime.calls, [])

    async def test_failure_keeps_original_context_and_runtime(self):
        for command in ("/model unknown", "/effort invalid", "/model terra", "/effort high"):
            with self.subTest(command=command):
                app, runtime = self.saved_app()
                old = app.state
                runtime.use = AsyncMock(side_effect=RuntimeError("provider rebuild failed"))
                self.assertFalse(await app.submit(command))
                self.assertIs(app.state, old)
                self.assertEqual(app.current_thread_id, "old-thread")
                self.assertEqual(app.active_task_id, "old-task")
                self.assertEqual(runtime.current.profile, "gpt56_sol")
                self.assertEqual(runtime.current.reasoning_effort, "medium")

    async def test_running_task_blocks_both_picker_and_typed_switch(self):
        app, runtime = self.saved_app()
        app._run_task = SimpleNamespace(done=lambda: False)
        for command in ("/model terra", "/effort high", "/mode model terra", "/mode effort high"):
            self.assertFalse(await app.submit(command))
        app.input.replace("/effort ")
        items, _ = runtime_picker_items(app)
        self.assertTrue(all(not item.enabled for item in items))
        self.assertEqual(runtime.calls, [])
        self.assertEqual(app.active_task_id, "old-task")

    async def test_saved_task_picker_explains_and_opens_new_conversation(self):
        app, runtime = self.saved_app()
        app.input.replace("/effort ")
        items, _ = runtime_picker_items(app)
        self.assertTrue(all(item.enabled for item in items))
        self.assertIn("Opens a new conversation", items[1].detail)
        await app.handle_key("down")
        await app.handle_key("\r")
        self.assertEqual(runtime.current.reasoning_effort, "low")
        self.assertIsNone(app.current_thread_id)
        self.assertIsNone(app.active_task_id)

    async def test_next_prompt_uses_no_old_thread(self):
        app, _ = self.saved_app()
        seen = []

        async def ask(prompt, **kwargs):
            seen.append((prompt, kwargs["thread_id"]))
            if False:
                yield

        app.controller.ask = ask
        self.assertTrue(await app.submit("/model terra"))
        self.assertTrue(await app.submit("fresh prompt"))
        await app._run_task
        self.assertEqual(seen, [("fresh prompt", None)])

    async def test_explicit_new_uses_same_empty_state(self):
        app, runtime = self.saved_app()
        self.assertTrue(await app.submit("/new"))
        self.assertIsNone(app.current_thread_id)
        self.assertIsNone(app.active_task_id)
        self.assertEqual(app.state.transcript, [])
        self.assertEqual(runtime.calls, [])

    async def test_saved_task_survives_switch_and_new_task_freezes_new_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            app, runtime = self.saved_app()
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")

            def facts():
                value = runtime.current
                return (value.profile, value.model, "chat_completions", "example.test",
                        value.topology, value.reasoning_effort, "medium", "b" * 64)

            async def resolve(contract):
                runtime.current.profile = contract.profile_id
                runtime.current.reasoning_effort = contract.reasoning_effort

            tasks = ForegroundTaskController(
                app.controller, repository, directory,
                profile_supplier=facts, runtime_resolver=resolve,
            )
            app.tasks = tasks
            old = await tasks.start("old objective")
            _ = [event async for event in tasks.events(old.id)]
            await tasks.steer(old.id, "saved old message")
            await tasks.pause(old.id)
            saved = await repository.load_task(old.id)
            messages = await repository.load_messages(old.thread_id)
            app.active_task_id, app.current_thread_id = old.id, old.thread_id
            self.assertTrue(await app.submit("/effort high"))
            self.assertTrue(await app.submit("fresh objective"))
            await app._run_task
            fresh = await repository.load_task(app.active_task_id)
            self.assertNotEqual(fresh.id, old.id)
            self.assertNotEqual(fresh.thread_id, old.thread_id)
            self.assertEqual(fresh.contract.reasoning_effort, "high")
            self.assertEqual(await repository.load_messages(fresh.thread_id), ())
            self.assertEqual(await repository.load_task(old.id), saved)
            self.assertEqual(await repository.load_messages(old.thread_id), messages)
            app.history = repository
            self.assertTrue(await app.restore_thread(old.thread_id))
            self.assertEqual(app.active_task_id, old.id)
            self.assertEqual(runtime.current.reasoning_effort, "medium")

    async def test_restore_failure_preserves_current_conversation(self):
        from code_agent.interfaces.tests.test_history import InMemoryHistoryReader
        from code_agent.core.events import AgentEvent, EventKind

        app, runtime = self.saved_app()
        old = app.state
        app.history = InMemoryHistoryReader((), (
            AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": "unavailable-task", "status": "paused"}),
        ), (), ())
        app.tasks = SimpleNamespace(restore_runtime_settings=AsyncMock(side_effect=ValueError("unavailable")))
        self.assertFalse(await app.restore_thread("other-thread"))
        self.assertIs(app.state, old)
        self.assertEqual(app.current_thread_id, "old-thread")
        self.assertEqual(app.active_task_id, "old-task")
        self.assertEqual(runtime.current.reasoning_effort, "medium")
