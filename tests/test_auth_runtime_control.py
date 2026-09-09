import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.store import CredentialStore
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent_win.auth_runtime_control import AuthenticationRuntimeControl


class AuthRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = CredentialStore(Path(self.temporary.name) / "credentials.dat")
        self.profiles = {}
        self.control = AuthenticationRuntimeControl(
            self.profiles, lambda p: self.profiles.__setitem__(p.name, p), self.store)

    async def test_key_login_uses_hidden_callback_and_never_returns_secret(self):
        read = AsyncMock(return_value="private-api-key")
        display = []
        result = await self.control.login("openai api_key", display.append, read)
        self.assertEqual(self.store.get("openai", "api_key").access, "private-api-key")
        self.assertNotIn("private-api-key", result + repr(display))
        self.assertEqual(read.await_count, 1)
        self.assertTrue(any(v.startswith("openai:api_key ") for v, _ in self.control.switch_choices()))

    async def test_oauth_and_key_create_distinct_rebuildable_profiles(self):
        self.store.set("xai", Credential("oauth", "oauth-secret"))
        self.store.set("xai", Credential("api_key", "key-secret"))
        from code_agent.authentication.catalog import ModelCatalog
        model = ModelCatalog().models("xai")[0].id
        first = await self.control.switch(f"xai:oauth {model}")
        second = await self.control.switch(f"xai:api_key {model}")
        self.assertNotEqual(first, second)
        self.assertEqual(self.profiles[first].provider.auth_source.kind, "oauth")
        self.assertEqual(self.profiles[second].provider.auth_source.kind, "api_key")
        self.assertEqual(self.profiles[first].provider.auth_source.path, self.store.path)
        self.assertTrue(self.control.affects_profile("xai oauth", first))
        self.assertFalse(self.control.affects_profile("xai api_key", first))
        self.assertTrue(self.control.affects_profile("xai api_key", second))
        self.assertFalse(self.control.affects_profile("anthropic oauth", first))
        self.assertEqual(await self.control.switch(first), first)
        self.profiles.clear()
        await self.control.restore_profile(first)
        self.assertEqual(self.profiles[first].provider.auth_source.kind, "oauth")
        self.assertFalse((Path(self.temporary.name) / "config.toml").exists())

    async def test_oauth_callback_forwarding_and_choices_cached(self):
        read = AsyncMock(return_value="code")
        display = []
        with patch("code_agent.authentication.oauth.login", new=AsyncMock(
            return_value=Credential("oauth", "private-oauth"))) as login:
            await self.control.login("openai-codex device_code", display.append, read)
        self.assertIs(login.call_args.kwargs["read_input"], read)
        with patch.object(self.store, "status", side_effect=AssertionError("repeated DPAPI")):
            self.control.switch_choices()
            self.control.switch_choices()

    async def test_missing_auth_does_not_register_or_fall_back(self):
        self.store.set("xai", Credential("api_key", "key"))
        with self.assertRaises(AuthError):
            await self.control.switch("xai:oauth grok-any")
        self.assertEqual(self.profiles, {})
        with self.assertRaises(AuthError):
            await self.control.login("openai api_key secret-inline", print, AsyncMock())

    async def test_openrouter_browser_key_retains_oauth_slot_identity(self):
        from code_agent.authentication.catalog import ModelCatalog
        self.store.set("openrouter", Credential("oauth", "browser-key"))
        self.store.set("openrouter", Credential("api_key", "manual-key"))
        model = ModelCatalog().models("openrouter")[0].id
        oauth = await self.control.switch(f"openrouter:oauth {model}")
        api = await self.control.switch(f"openrouter:api_key {model}")
        self.assertTrue(self.control.affects_profile("openrouter oauth", oauth))
        self.assertFalse(self.control.affects_profile("openrouter oauth", api))
        self.assertFalse(self.control.affects_profile("openrouter api_key", oauth))
        self.assertTrue(self.control.affects_profile("openrouter api_key", api))

    async def test_cancel_during_commit_reports_actual_saved_result(self):
        entered, release = threading.Event(), threading.Event()
        original = self.store.set
        def delayed(provider, credential):
            entered.set()
            release.wait(3)
            original(provider, credential)
        with patch.object(self.store, "set", side_effect=delayed):
            task = asyncio.create_task(self.control.login("openai api_key", print, AsyncMock(return_value="key")))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                release.set()
                result = await asyncio.wait_for(task, 3)
                self.assertIn("Signed in", result)
            finally:
                release.set()
        self.assertEqual(self.store.get("openai", "api_key").access, "key")

    async def test_catalog_failure_does_not_misreport_committed_login(self):
        with patch.object(self.control, "_reload_choices", side_effect=ValueError("invalid catalog")):
            result = await self.control.login("openai api_key", print, AsyncMock(return_value="key"))
        self.assertIn("Signed in", result)
        self.assertIsNotNone(self.store.get("openai", "api_key"))

    async def test_registered_profile_builds_only_after_explicit_manager_switch(self):
        original = ModelProfile("original", ProviderConfig(
            "https://api.example.test", "model", ApiProtocol.RESPONSES, "KEY"), 1000, 100)
        client = type("Client", (), {"aclose": AsyncMock()})()
        built = []
        async def build(profile):
            built.append(profile.name)
            return ProviderRuntime(profile, client, object())
        manager = ProviderRuntimeManager({original.name: original},
            ProviderRuntime(original, client, object()), build, lambda runner: None)
        self.control = AuthenticationRuntimeControl(self.profiles, manager.register_profile, self.store)
        self.store.set("openai", Credential("api_key", "key"))
        name = await self.control.switch("openai:api_key gpt-4.1")
        self.assertEqual(built, [])
        self.assertEqual(manager.current.profile.name, "original")
        await manager.switch(name, idle=True)
        self.assertEqual(built, [name])
        self.assertEqual(manager.current.profile.provider.auth_source.kind, "api_key")
        await manager.aclose()
