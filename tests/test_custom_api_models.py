import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.authentication.store import CredentialStore
from code_agent.providers.config import ModelProfile
from code_agent.providers.tests.test_model_discovery import config
from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app
from chaos_agent.auth_runtime_control import AuthenticationRuntimeControl


class CustomApiModelsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = ModelProfile("custom", config(), 64000, 8000)
        self.profiles = {"custom": self.base}
        self.auth = AuthenticationRuntimeControl(self.profiles, lambda p: self.profiles.__setitem__(p.name, p),
                     CredentialStore(Path(self.temp.name) / "credentials.dat"))

    async def test_explicit_load_nested_select_and_offline_restore(self):
        self.assertIn("api:custom", [value for value, _ in self.auth.model_choices()])
        with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock(return_value=("org/model", "seed"))) as discover:
            self.assertEqual(discover.await_count, 0)
            await self.auth.refresh_models("api:custom")
            self.assertEqual(discover.await_count, 1)
        self.assertTrue(self.auth.is_saved_model_choice("api:custom org/model"))
        name = await self.auth.select_model("api:custom org/model")
        selected = self.profiles[name]
        self.assertEqual(selected.provider.model, "org/model")
        self.assertEqual(selected.context_window, 64000)
        self.assertEqual(selected.max_output_tokens, 8000)
        self.assertIs(selected.provider.api_key_source, self.base.provider.api_key_source)
        self.assertEqual(await self.auth.select_model("api:custom seed"), "custom")
        del self.profiles[name]
        await self.auth.restore_profile(name)
        self.assertEqual(self.profiles[name], selected)

    async def test_picker_load_is_async_then_selection_opens_new_conversation(self):
        runtime = Runtime()
        app = make_app(runtime_selection=runtime)
        app.authentication = self.auth
        app.current_thread_id = "previous"
        with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock(return_value=("model-a",))):
            self.assertTrue(await app.submit("/model api:custom"))
            if app._auth_task is not None:
                await asyncio.wait_for(app._auth_task, 3)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(app.input.text, "/model api:custom ")
        app.interactions.rows(app)
        self.assertIn("api:custom model-a", [item.identifier for item in app.interactions.picker.matches])
        self.assertTrue(await app.submit("/model api:custom model-a"))
        self.assertEqual(runtime.current.profile, "discovered/custom/model-a")
        self.assertIsNone(app.current_thread_id)

    async def test_failure_keeps_previous_catalog_and_runtime(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = self.auth
        with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock(return_value=("model-a",))):
            await self.auth.refresh_models("api:custom")
        with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock(side_effect=ValueError("secret"))):
            await app.submit("/model api:custom")
            if app._auth_task is not None:
                await app._auth_task
        self.assertEqual(app.runtime_selection.calls, [])
        self.assertIn("api:custom model-a", [value for value, _ in self.auth.model_choices_for("api:custom ")])
        self.assertNotIn("secret", "\n".join(e.text for e in app.state.entries))

    async def test_busy_loading_is_rejected(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = self.auth
        app._run_task = asyncio.create_task(asyncio.sleep(10))
        try:
            with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock()) as discover:
                self.assertFalse(await app.submit("/model api:custom"))
                discover.assert_not_awaited()
        finally:
            app._run_task.cancel()
            await asyncio.gather(app._run_task, return_exceptions=True)

    async def test_cancel_does_not_replace_catalog(self):
        app = make_app(runtime_selection=Runtime())
        app.authentication = self.auth
        pending = asyncio.Event()
        async def discover(_):
            await pending.wait()
        with patch("chaos_agent.api_model_control.discover_models", side_effect=discover):
            await app.submit("/model api:custom")
            task = app._auth_task
            task.cancel()
            await task
        self.assertEqual(app.runtime_selection.calls, [])
        self.assertEqual(len(self.auth.model_choices_for("api:custom ")), 1)

    async def test_last_selected_api_model_restores_without_discovery(self):
        from chaos_agent.model_selection_preference import ModelSelectionPreference, ModelSelectionPreferenceStore
        from tests.test_model_selection_preference import _application
        name = "discovered/custom/org%2Fmodel"
        store = ModelSelectionPreferenceStore(Path(self.temp.name))
        store.save(ModelSelectionPreference.configured(name))
        runtime = Runtime()
        runtime.profiles = lambda: tuple((n, p.provider.model, p.provider.api.value) for n, p in self.profiles.items())
        application = _application(store, runtime, self.auth)
        with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock()) as discover:
            await application.startup()
            discover.assert_not_awaited()
        self.assertEqual(runtime.current.profile, name)
        self.assertEqual(self.profiles[name].provider.model, "org/model")

    async def test_real_application_registers_and_switches_discovered_profile(self):
        from tests.test_tui_repair_integration import application_fixture
        with application_fixture() as app:
            try:
                source = app.runtime_selection.current.profile
                choice = "api:" + source
                with patch("chaos_agent.api_model_control.discover_models", new=AsyncMock(return_value=("custom-new",))):
                    await app.tui.submit("/model " + choice)
                    if app.tui._auth_task is not None:
                        await app.tui._auth_task
                self.assertTrue(await app.tui.submit("/model " + choice + " custom-new"))
                self.assertEqual(app.runtime_selection.current.model, "custom-new")
                self.assertEqual(app.model.current.profile.provider.model, "custom-new")
                self.assertEqual(app.model_preferences.load().profile, app.runtime_selection.current.profile)
            finally:
                await app.aclose()
