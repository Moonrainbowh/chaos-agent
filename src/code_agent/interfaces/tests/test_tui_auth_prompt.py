from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_auth_prompt import auth_input_view, cancel_auth_input, read_auth_input
from code_agent.interfaces.tui_lifecycle import close_tasks
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class AuthInputTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        self.output = []
        return WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=self.output.append)

    async def start_prompt(self, app):
        app._auth_task = asyncio.create_task(read_auth_input(app, "API key:"))
        await asyncio.sleep(0)
        return app._auth_task

    async def test_secret_typing_and_paste_never_enter_history_or_output(self):
        app = self.make_app()
        app.input.insert("ordinary draft")
        task = await self.start_prompt(app)
        try:
            await app.handle_key("secret-")
            await app.handle_key("\x1b[200~api-key\r\n\x1b[201~")
            self.assertFalse(task.done())
            self.assertEqual(auth_input_view(app)[0], "*" * len("secret-api-key"))
            self.assertEqual(app.input.text, "ordinary draft")
            self.assertEqual(app.input._history, [])
            self.assertEqual(app.input._submitted.text, "")
            self.assertNotIn("secret-", "".join(self.output))
            self.assertNotIn("api-key", "".join(self.output))
            self.assertEqual(app.state.entries, [])
            await app.handle_key("\r")
            self.assertEqual(await task, "secret-api-key")
            self.assertIsNone(app._auth_prompt)
            self.assertEqual(app.input.text, "ordinary draft")
        finally:
            await cancel_auth_input(app)

    async def test_escape_cancels_and_clears_private_paste(self):
        app = self.make_app()
        task = await self.start_prompt(app)
        state = app._auth_prompt
        await app.handle_key("\x1b[200~secret-key\x1b[201~")
        await app.handle_key("\x1b")
        self.assertTrue(task.cancelled())
        self.assertIsNone(app._auth_prompt)
        self.assertEqual(state.characters, [])
        self.assertNotIn("secret-key", "".join(self.output))

    async def test_waiting_for_browser_blocks_keys_and_programmatic_submission(self):
        app = self.make_app()
        app._auth_task = asyncio.create_task(asyncio.Event().wait())
        try:
            await app.handle_key("secret-before-prompt")
            await app.handle_key("\r")
            self.assertFalse(await app.submit("secret-direct-submit"))
            self.assertEqual(app.input.text, "")
            self.assertEqual(app.state.entries, [])
            self.assertIsNone(app._run_task)
            self.assertNotIn("secret", "".join(self.output))
        finally:
            await cancel_auth_input(app)

    async def test_ctrl_c_cancels_login_without_exiting_app(self):
        app = self.make_app()
        app.running = True
        task = await self.start_prompt(app)
        await app.handle_key("\x03")
        self.assertTrue(task.cancelled())
        self.assertTrue(app.running)

    async def test_editing_does_not_recall_conversation_history(self):
        app = self.make_app()
        app.input.insert("history")
        app.input.submit()
        task = await self.start_prompt(app)
        await app.handle_key("ac")
        await app.handle_key("left")
        await app.handle_key("b")
        await app.handle_key("up")
        await app.handle_key("\r")
        self.assertEqual(await task, "abc")
        self.assertEqual([item.text for item in app.input._history], ["history"])

    async def test_multiline_or_control_paste_is_rejected_without_echo(self):
        app = self.make_app()
        task = await self.start_prompt(app)
        try:
            await app.handle_key("\x1b[200~secret\nother\x1b[201~")
            self.assertEqual(app._auth_prompt.characters, [])
            self.assertIn("single-line", auth_input_view(app)[2][1])
            self.assertNotIn("secret", "".join(self.output))
        finally:
            await cancel_auth_input(app)

    async def test_app_close_cancels_prompt_and_releases_private_buffer(self):
        app = self.make_app()
        task = await self.start_prompt(app)
        state = app._auth_prompt
        await app.handle_key("secret-at-close")
        with patch("code_agent.interfaces.tui_lifecycle.exit_transition", new=AsyncMock()):
            await close_tasks(app)
        self.assertTrue(task.cancelled())
        self.assertEqual(state.characters, [])
        self.assertIsNone(app._auth_prompt)
        self.assertNotIn("secret-at-close", "".join(self.output))
