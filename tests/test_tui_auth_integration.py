"""Exercise login keys and switching through the real application composition."""
import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from code_agent.authentication.store import CredentialStore
from code_agent.config.loader import RuntimeConfig
from code_agent.policy.models import ApprovalMode
from code_agent_win.app import create_application
from tests.test_runtime_selection import _profiles


class TuiAuthIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_login_switch_back_and_restore_runtime(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            container = Path(temporary).resolve()
            root, state = container / "workspace", container / "state"
            root.mkdir()
            state.mkdir()
            profiles = _profiles()
            config_file = state / "config.toml"
            config_file.write_text("# persistent default unchanged\n", encoding="utf-8")
            runtime = RuntimeConfig(profiles["sol"].provider, "sol", ApprovalMode.AUTO,
                                    False, config_file, tuple(profiles.values()))
            for target, value in (
                ("_session_path", state / "sessions.sqlite3"),
                ("_product_state_root", state),
                ("_workspace_storage_path", container / "managed-workspaces"),
                ("load_runtime_config", runtime),
            ):
                stack.enter_context(patch("code_agent_win.app." + target, return_value=value))
            stack.enter_context(patch("code_agent_win.app._model_client", side_effect=lambda _: object()))
            stack.enter_context(patch.dict("os.environ", {"CHAOS_AUTH_FILE": str(state / "credentials.dat")}))
            application = create_application(root)
            try:
                await self._exercise(application, state)
                self.assertEqual(config_file.read_text(encoding="utf-8"), "# persistent default unchanged\n")
            finally:
                await application.aclose()

    async def _exercise(self, application, state):
        tui = application.tui
        output = []
        tui._write = output.append
        self.assertTrue(await tui.submit("/login openai api_key"))
        self.assertIsNotNone(tui._auth_prompt)
        login_task = tui._auth_task
        await tui.handle_key("\x1b[200~private-integration-key\x1b[201~")
        await tui.handle_key("\r")
        await asyncio.wait_for(login_task, 5)
        self.assertNotIn("private-integration-key", "".join(output))
        self.assertEqual(CredentialStore(state / "credentials.dat").get("openai", "api_key").access,
                         "private-integration-key")
        self.assertTrue(await tui.submit("/logswitch openai:api_key gpt-4.1"))
        name = "login/openai/api_key/gpt-4.1"
        self.assertEqual(application.model.current.profile.name, name)
        self.assertEqual(application.model.current.profile.provider.auth_source.kind, "api_key")
        self.assertEqual(application.mode.profile_id, name)
        task = await application.foreground_tasks.start("freeze authenticated runtime")
        self.assertEqual(task.contract.profile_id, name)
        tui.current_thread_id = "existing-login-thread"
        tui.active_task_id = task.id
        self.assertTrue(await tui.submit("/login openai api_key"))
        login_task = tui._auth_task
        await tui.handle_key("replacement-private-key")
        await tui.handle_key("\r")
        await asyncio.wait_for(login_task, 5)
        self.assertIsNone(tui.current_thread_id)
        self.assertIsNone(tui.active_task_id)
        self.assertNotIn("replacement-private-key", "".join(output))
        self.assertTrue(await tui.submit("/logswitch sol"))
        self.assertEqual(application.model.current.profile.name, "sol")
        await application.foreground_tasks._runtime_resolver(task.contract)
        self.assertEqual(application.model.current.profile.name, name)
