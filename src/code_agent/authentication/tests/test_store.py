import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.profile_setup import append_profile
from code_agent.authentication.source import StoredCredentialSource
from code_agent.authentication.store import CredentialStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "auth.dat"
        self.store = CredentialStore(self.path)

    def test_roundtrip_two_platforms_logout_preserves_other(self):
        first = Credential("oauth", "secret-first", "refresh-secret", 42, {"accountId": "a"})
        second = Credential("api_key", "secret-second")
        self.store.set("openai-codex", first)
        self.store.set("openrouter", second)
        self.assertEqual(self.store.get("openai-codex"), first)
        self.assertNotIn("secret", repr(first))
        self.assertNotIn("secret", repr(self.store.status()))
        if os.name == "nt":
            self.assertNotIn(b"secret", self.path.read_bytes())
        self.assertTrue(self.store.remove("openai-codex"))
        self.assertEqual(self.store.get("openrouter"), second)

    def test_corruption_is_preserved(self):
        self.path.write_bytes(b"broken-sensitive-text")
        with self.assertRaises(AuthError) as caught:
            self.store.set("xai", Credential("api_key", "abc"))
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), b"broken-sensitive-text")

    def test_profile_append_preserves_comments_and_existing_default(self):
        path = Path(self.temp.name) / "config.toml"
        original = '# keep me\n[default]\nprovider="old"\n[providers.old]\nmodel="old-model"\n'
        path.write_text(original, encoding="utf-8")
        fields = {"model": "new-model", "context_window": 1000, "max_output_tokens": 100}
        append_profile(path, "new", fields)
        self.assertTrue(path.read_text(encoding="utf-8").startswith(original))
        with self.assertRaises(AuthError):
            append_profile(path, "new", fields)


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_is_persisted_and_second_read_uses_new_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.dat"
            store = CredentialStore(path)
            store.set("xai", Credential("oauth", "old", "refresh", time.time() - 1))
            source = StoredCredentialSource("xai", path)
            refreshed = Credential("oauth", "new", "rotated", time.time() + 3600)
            with patch("code_agent.authentication.oauth.refresh", new=AsyncMock(return_value=refreshed)) as refresh:
                first, second = await asyncio.gather(source.resolve(), source.resolve())
            self.assertEqual(first, refreshed)
            self.assertEqual(second, refreshed)
            self.assertEqual(refresh.await_count, 1)
            self.assertEqual(store.get("xai"), refreshed)

    async def test_logout_and_kind_mismatch_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.dat"
            store = CredentialStore(path)
            store.set("xai", Credential("api_key", "key"))
            with self.assertRaises(AuthError):
                await StoredCredentialSource("xai", path).resolve()
            store.remove("xai")
            with self.assertRaises(AuthError):
                await StoredCredentialSource("xai", path, "api_key").resolve()
