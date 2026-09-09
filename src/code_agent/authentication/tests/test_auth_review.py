"""Regression checks for credential coexistence and cancelled refresh ownership."""
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.source import StoredCredentialSource
from code_agent.authentication.store import CredentialStore


class AuthReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_provider_credentials_coexist_and_logout_is_selective(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credentials.dat")
            oauth = Credential("oauth", "oauth-private", "refresh-private", time.time() + 3600)
            key = Credential("api_key", "key-private")
            store.set("xai", oauth)
            store.set("xai", key)
            self.assertEqual(await StoredCredentialSource("xai", store.path).resolve(), oauth)
            self.assertEqual(await StoredCredentialSource("xai", store.path, "api_key").resolve(), key)
            self.assertTrue(store.remove("xai", "oauth"))
            with self.assertRaises(AuthError):
                await StoredCredentialSource("xai", store.path).resolve()
            self.assertEqual(store.get("xai", "api_key"), key)

    async def test_cancelled_refresh_cannot_resurrect_logged_out_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credentials.dat")
            store.set("xai", Credential("oauth", "old", "refresh", time.time() - 1))
            entered, release = threading.Event(), threading.Event()

            async def refresh(provider, credential):
                entered.set()
                await asyncio.to_thread(release.wait, 5)
                return Credential("oauth", "new", "rotated", time.time() + 3600)

            with patch("code_agent.authentication.oauth.refresh", new=refresh):
                task = asyncio.create_task(StoredCredentialSource("xai", store.path).resolve())
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    logout = asyncio.create_task(asyncio.to_thread(store.remove, "xai"))
                    release.set()
                    self.assertTrue(await asyncio.wait_for(logout, 3))
                finally:
                    release.set()
            self.assertIsNone(store.get("xai"))
            store.set("xai", Credential("api_key", "still-writable"))
            self.assertEqual(store.get("xai").access, "still-writable")

    async def test_refresh_failure_retains_both_credentials_without_key_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credentials.dat")
            old = Credential("oauth", "old", "refresh", time.time() - 1)
            key = Credential("api_key", "other-key")
            store.set("xai", old)
            store.set("xai", key)

            async def fail(provider, credential):
                raise AuthError("Refresh denied")

            with patch("code_agent.authentication.oauth.refresh", new=fail):
                with self.assertRaisesRegex(AuthError, "Refresh denied"):
                    await StoredCredentialSource("xai", store.path).resolve()
            self.assertEqual(store.get("xai", "oauth"), old)
            self.assertEqual(store.get("xai", "api_key"), key)
