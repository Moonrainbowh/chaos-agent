from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.models import ActionRequest, ModelEvent, ModelEventKind, ToolCall
from code_agent.core.task import TaskStatus
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent_win.app import _product_state_root, _session_path
from tests.agent_app_test_support import (
    FakeModel,
    _BlockingRuntime,
    _RecordingRuntime,
    _collect_events,
    _configured_application,
    _task_context,
    _task_dispatcher,
)


class ApplicationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_guard_rejects_external_path_before_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, root, _ = _configured_application(Path(temporary).resolve())
            outside = root.parent / "outside.txt"
            capture = application.dispatcher.capture
            with patch.object(
                capture, "apply_edit", new=AsyncMock()
            ) as apply_edit:
                result = await application.dispatcher.dispatch(
                    ActionRequest(
                        "external", "write_file",
                        {"path": str(outside), "content": "forbidden"},
                    ),
                    CancellationToken(),
                )
            self.assertTrue(result.is_error)
            self.assertFalse(outside.exists())
            apply_edit.assert_not_awaited()

    def test_session_path_copies_the_legacy_data_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "code-agent" / "sessions.sqlite3"
            legacy.parent.mkdir()

            connection = sqlite3.connect(legacy)
            try:
                connection.execute("CREATE TABLE threads (id TEXT)")
                connection.commit()
            finally:
                connection.close()

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                current = _session_path()

            self.assertEqual(current, root / "chaos-agent" / "sessions.sqlite3")
            self.assertTrue(current.exists())
            self.assertTrue(legacy.exists())

    def test_session_path_uses_the_chaos_agent_directory_for_new_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                current = _session_path()

            self.assertEqual(current, root / "chaos-agent" / "sessions.sqlite3")

    def test_product_state_root_uses_the_chaos_agent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                current = _product_state_root()

            self.assertEqual(current, root / "chaos-agent")

    async def test_foreground_task_repairs_a_failed_test_then_checkpoints_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("before\n", encoding="utf-8")
            runtime = _RecordingRuntime((1, 0))
            dispatcher = _task_dispatcher(root, runtime)
            calls = (
                ToolCall("read", "read_file", {"path": "note.txt"}),
                ToolCall("write-1", "write_file", {"path": "note.txt", "content": "broken\n"}),
                ToolCall("test-1", "run_verification", {"kind": "python_unittest"}),
                ToolCall("write-2", "write_file", {"path": "note.txt", "content": "fixed\n"}),
                ToolCall("test-2", "run_verification", {"kind": "python_unittest"}),
            )
            model = FakeModel(tuple(
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED))
                for call in calls
            ) + ((ModelEvent(ModelEventKind.TEXT_DELTA, text="fixed and verified"), ModelEvent(ModelEventKind.COMPLETED)),))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    dispatcher,
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
            task = await controller.start("repair note")

            events = [event async for event in controller.events(task.id)]
            stored = await sessions.load_task(task.id)

            self.assertEqual(stored.status, TaskStatus.COMPLETED)
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "fixed\n")
            self.assertIn("note.txt", (await sessions.load_task_state(task.thread_id)).files_changed)
            self.assertGreaterEqual(len(await sessions.list_checkpoints(task.thread_id)), 3)
            self.assertEqual((await sessions.load_task_budget(task.id)).repair_cycles, 1)
            self.assertEqual(len(runtime.commands), 2)
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)

    async def test_current_verification_evidence_expires_after_a_later_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("before\n", encoding="utf-8")
            runtime = _RecordingRuntime((0,))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            model = FakeModel((
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("verify", "run_verification", {"kind": "python_unittest"})), ModelEvent(ModelEventKind.COMPLETED)),
                (ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("write", "write_file", {"path": "note.txt", "content": "after\n"})), ModelEvent(ModelEventKind.COMPLETED)),
                (ModelEvent(ModelEventKind.TEXT_DELTA, text="done"), ModelEvent(ModelEventKind.COMPLETED)),
            ))
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    _task_dispatcher(root, runtime),
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
            task = await controller.start("verify then edit")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.VERIFYING)
            self.assertNotIn(EventKind.COMPLETED, [event.kind for event in events])

    async def test_model_completion_automatically_runs_discovered_project_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.0.0'\n", encoding="utf-8")
            runtime = _RecordingRuntime((0,))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            model = FakeModel(((
                ModelEvent(ModelEventKind.TEXT_DELTA, text="done"),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            controller = ForegroundTaskController(
                AgentController(AgentEngine(
                    model,
                    _task_context(root),
                    _task_dispatcher(root, runtime),
                    sessions,
                    verification=LedgerTaskVerificationService(root, sessions),
                )),
                sessions,
                root,
            )
            task = await controller.start("repair project")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.COMPLETED)
            self.assertEqual(len(runtime.commands), 1)
            self.assertIn("-m unittest discover", runtime.commands[0])
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)
            messages = await sessions.load_messages(task.thread_id)
            self.assertEqual([message.role for message in messages[-3:]], ["assistant", "assistant", "tool"])
            self.assertEqual(messages[-2].tool_calls[0].id, messages[-1].tool_call_id)
            self.assertEqual(model.streams, [])

    async def test_task_boundary_waits_for_decision_without_starting_network_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = _RecordingRuntime(())
            dispatcher = _task_dispatcher(root, runtime)
            model = FakeModel(((
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("install", "run_command", {"command": "pip install package"})),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(
                AgentController(AgentEngine(model, _task_context(root), dispatcher, sessions)),
                sessions,
                root,
            )
            task = await controller.start("install package")

            events = [event async for event in controller.events(task.id)]

            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.WAITING_DECISION)
            self.assertEqual(runtime.commands, [])
            self.assertIn(EventKind.TASK_DECISION_REQUIRED, [event.kind for event in events])

    async def test_resume_never_replays_an_interrupted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            database = root / "sessions.sqlite3"
            first_runtime = _BlockingRuntime()
            first_model = FakeModel(((
                ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall("test", "run_verification", {"kind": "python_unittest"})),
                ModelEvent(ModelEventKind.COMPLETED),
            ),))
            sessions = SQLiteSessionRepository(database)
            first_controller = ForegroundTaskController(
                AgentController(AgentEngine(first_model, _task_context(root), _task_dispatcher(root, first_runtime), sessions)),
                sessions,
                root,
            )
            task = await first_controller.start("run tests")
            running = asyncio.create_task(_collect_events(first_controller.events(task.id)))
            await first_runtime.started.wait()
            await first_controller.pause(task.id, "terminal closed")
            await running

            resumed_runtime = _RecordingRuntime(())
            resumed = ForegroundTaskController(
                AgentController(AgentEngine(
                    FakeModel(((ModelEvent(ModelEventKind.TEXT_DELTA, text="rechecked"), ModelEvent(ModelEventKind.COMPLETED)),)),
                    _task_context(root), _task_dispatcher(root, resumed_runtime), SQLiteSessionRepository(database),
                )),
                SQLiteSessionRepository(database),
                root,
            )

            events = [event async for event in resumed.resume(task.id, "recheck workspace safely")]

            self.assertEqual(len(first_runtime.commands), 1)
            self.assertIn("-m unittest discover -s .", first_runtime.commands[0])
            self.assertEqual(resumed_runtime.commands, [])
            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.VERIFYING)
            self.assertGreaterEqual(len(await sessions.list_checkpoints(task.thread_id)), 2)
            self.assertNotEqual(events[-1].kind, EventKind.COMPLETED)


if __name__ == "__main__":
    unittest.main()
