import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.authentication.catalog import CatalogModel
from code_agent.authentication.models import Credential
from code_agent.authentication.store import CredentialStore
from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app
from code_agent_win.auth_runtime_control import AuthenticationRuntimeControl


class WorkBuddySwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CredentialStore(Path(self.temp.name) / "credentials.dat")
        self.store.set("workbuddy", Credential("oauth", "test-secret", extra={
            "workbuddyEndpoint": "https://copilot.tencent.com", "workbuddyAccount": {"uid": "test"}}))
        self.profiles = {}
        self.control = AuthenticationRuntimeControl(self.profiles, lambda p: self.profiles.__setitem__(p.name, p), self.store)
        self.model = CatalogModel("cloud-test", "Cloud", "workbuddy", "chat_completions",
                                  "https://copilot.tencent.com/v2", "/chat/completions", 100000, 16000)

    async def test_saved_login_without_seed_is_visible_and_loads_real_models(self):
        choices = self.control.switch_choices()
        self.assertIn("workbuddy:oauth", [v for v, _ in choices])
        with patch("code_agent.authentication.workbuddy_catalog.discover", new=AsyncMock(return_value=(self.model,))):
            self.assertEqual(await self.control.refresh_models("workbuddy:oauth"), 1)
        self.assertEqual(self.control.switch_choices()[0][0], "workbuddy:oauth cloud-test")
        name = await self.control.switch("workbuddy:oauth cloud-test")
        profile = self.profiles[name]
        self.assertEqual(profile.provider.api.value, "chat_completions")
        self.assertEqual(profile.context_window, 100000)
        self.assertEqual(profile.provider.auth_source.kind, "oauth")

    async def test_picker_enter_loads_then_offers_model_without_changing_runtime(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = self.control
        app.input.replace("/logswitch work")
        with patch("code_agent.authentication.workbuddy_catalog.discover", new=AsyncMock(return_value=(self.model,))):
            await app.handle_key("\r")
            loading = app._auth_task
            await asyncio.wait_for(loading, 3)
        self.assertEqual(app.input.text, "/logswitch workbuddy:oauth ")
        self.assertIn("cloud-test", "\n".join(app.interactions.rows(app)))
        self.assertEqual(app.runtime_selection.current.profile, "gpt56_sol")

    async def test_discovery_failure_retains_login_and_a_visible_retry_choice(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = self.control
        with patch("code_agent.authentication.workbuddy_catalog.discover", new=AsyncMock(side_effect=ValueError("private"))):
            self.assertTrue(await app.submit("/logswitch workbuddy:oauth"))
            await app._auth_task
        text = "\n".join(e.text for e in app.state.entries)
        self.assertIn("discovery failed", text)
        self.assertNotIn("private", text)
        self.assertIsNotNone(self.store.get("workbuddy", "oauth"))
        self.assertEqual(app.runtime_selection.current.profile, "gpt56_sol")
