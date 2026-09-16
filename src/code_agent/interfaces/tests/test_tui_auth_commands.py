from __future__ import annotations

import asyncio
import unittest

from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class Authentication:
    def __init__(self):
        self.calls = []
        self.fail = False

    def login_choices(self):
        return (("openai-codex browser", "OAuth"), ("openai api_key", "API key"))

class AuthCommandTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = Authentication()
        return app

    async def test_commands_require_injected_authentication(self):
        self.assertEqual(parse_tui_command("/login openai api_key").command.kind, TuiCommandKind.LOGIN)
        self.assertIsNone(parse_tui_command(":logswitch p").command)
        app = make_app(runtime_selection=Runtime())
        self.assertFalse(await app.submit("/login"))

    async def test_model_picker_opens_new_conversation_only_on_change(self):
        app = self.app()
        app.current_thread_id = "old-thread"
        app.active_task_id = "paused-task"
        app.input.replace("/model")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/model ")
        self.assertIn("Current", "\n".join(app.interactions.rows(app)))
        await app.handle_key("\r")
        self.assertEqual(app.current_thread_id, "old-thread")
        self.assertTrue(await app.submit("/model gpt56_terra"))
        self.assertIsNone(app.current_thread_id)
        self.assertIsNone(app.active_task_id)
        self.assertEqual(app.runtime_selection.current.profile, "gpt56_terra")

    async def test_removed_logswitch_cannot_change_the_runtime(self):
        app = self.app()
        app.current_thread_id = "old-thread"
        self.assertFalse(await app.submit("/logswitch gpt56_terra"))
        self.assertEqual(app.current_thread_id, "old-thread")
        self.assertEqual(app.runtime_selection.current.profile, "gpt56_sol")

    async def test_busy_model_switch_is_blocked(self):
        app = self.app()
        app._run_task = asyncio.create_task(asyncio.Event().wait())
        try:
            self.assertFalse(await app.submit("/model gpt56_terra"))
            self.assertEqual(app.runtime_selection.calls, [])
        finally:
            app._run_task.cancel()
            await asyncio.gather(app._run_task, return_exceptions=True)

    async def test_login_picker_exposes_methods(self):
        app = self.app()
        app.input.replace("/login")
        await app.handle_key("\r")
        self.assertEqual(app.input.text, "/login ")
        self.assertIn("OAuth", "\n".join(app.interactions.rows(app)))
