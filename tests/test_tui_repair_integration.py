from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from code_agent.config.loader import RuntimeConfig
from code_agent.core.models import ModelEvent, ModelEventKind
from code_agent.core.task import TaskStatus
from code_agent.policy.models import ApprovalMode
from code_agent_win.app import create_application
from tests.test_runtime_selection import _profiles


class ReplyModel:
    async def stream(self, *args, **kwargs):
        yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Hello.")
        yield ModelEvent(ModelEventKind.COMPLETED)


@contextmanager
def application_fixture():
    profiles = _profiles()
    config = RuntimeConfig(
        provider=profiles["sol"].provider, profile="sol",
        approval_mode=ApprovalMode.AUTO, allow_sensitive_paths=False,
        config_path=Path("missing.toml"), profiles=tuple(profiles.values()),
    )
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        container = Path(temporary).resolve()
        root, state = container / "workspace", container / "state"
        root.mkdir()
        state.mkdir()
        for name, value in (
            ("_session_path", state / "sessions.sqlite3"),
            ("_product_state_root", state),
            ("_workspace_storage_path", container / "managed-workspaces"),
            ("load_runtime_config", config),
        ):
            stack.enter_context(patch("code_agent_win.app." + name, return_value=value))
        stack.enter_context(patch("code_agent_win.app._model_client", side_effect=lambda _: ReplyModel()))
        app = create_application(root)
        app.tui._write = lambda _: None
        app.tui.play_sound = lambda **_: None
        yield app


class TuiRepairIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_finishes_without_verification_or_semantic_refresh(self):
        with application_fixture() as app:
            try:
                refresh = Mock(side_effect=AssertionError("Q&A must not refresh the verification graph"))
                app.controller._engine._verification._semantic_snapshot = refresh
                self.assertTrue(await app.tui.submit("你好呀嘻嘻嘻"))
                await app.tui.wait_idle()
                records = await app.sessions.list_tasks(include_terminal=True)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].status, TaskStatus.COMPLETED)
                self.assertEqual(records[0].contract.intent.value, "analyze")
                self.assertEqual(await app.sessions.list_verification_evidence(records[0].id), ())
                self.assertIsNone(app.tui.active_task_id)
                self.assertEqual(app.tui.state.status, "completed")
                refresh.assert_not_called()
            finally:
                await app.aclose()

    async def test_keyboard_selection_rebuilds_real_runtime_and_freezes_next_task(self):
        with application_fixture() as app:
            try:
                await app.tui.submit("hello")
                await app.tui.wait_idle()
                app.tui.input.replace("/model")
                await app.tui.handle_key("\r")
                await app.tui.handle_key("down")
                await app.tui.handle_key("\r")
                self.assertEqual(app.model.current.profile.name, "terra")
                self.assertTrue(await app.tui.submit("/effort high"))
                await app.tui.submit("hello")
                await app.tui.wait_idle()
                records = await app.sessions.list_tasks(include_terminal=True)
                self.assertEqual(len(records), 2)
                self.assertTrue(all(item.status is TaskStatus.COMPLETED for item in records))
                selected = next(item for item in records if item.contract.profile_id == "terra")
                self.assertEqual(selected.contract.reasoning_effort, "high")
                self.assertEqual(app.runtime_selection.current.profile, "terra")
            finally:
                await app.aclose()

    async def test_unverified_modification_waits_with_reason_and_can_accept_partial(self):
        with application_fixture() as app:
            try:
                await app.tui.submit("implement the requested feature")
                await app.tui.wait_idle()
                task = (await app.sessions.list_tasks())[0]
                self.assertEqual(task.status, TaskStatus.WAITING_DECISION)
                self.assertIn("Missing", task.stop_reason)
                self.assertEqual(app.tui.state.status, "waiting_decision")
                self.assertFalse(await app.tui.submit("/model terra"))
                self.assertEqual(app.model.current.profile.name, "sol")
                self.assertTrue(await app.tui.submit("/accept"))
                self.assertEqual(app.tui.state.status, "accepted_partial")
                self.assertIsNone(app.tui.active_task_id)
            finally:
                await app.aclose()
