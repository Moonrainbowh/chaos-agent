from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from code_agent.authentication.models import Credential
from code_agent.authentication.store import CredentialStore
from code_agent.config.loader import load_runtime_config
from code_agent.core.models import Message, ModelEventKind
from code_agent.providers.errors import ProviderError
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent_win.cli import run
from code_agent_win.runtime_support import model_client, replace_model


class AuthCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = {"LOCALAPPDATA": str(self.root), "CHAOS_CONFIG": str(self.root / "config.toml"),
                            "CHAOS_AUTH_FILE": str(self.root / "credentials.dat"), "TEST_AUTH_KEY": "offline-secret"}
        self.patch = patch.dict(os.environ, self.environment, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.output = io.StringIO()
        self.errors = io.StringIO()

    async def command(self, *arguments):
        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
            return await run(("auth", *arguments))

    async def test_first_run_login_configure_inference_logout_without_application_startup(self):
        with patch("code_agent_win.cli.create_application", side_effect=AssertionError("should not start workspace")):
            self.assertEqual(await self.command("login", "openai", "--api-key-env", "TEST_AUTH_KEY"), 0)
            self.assertEqual(await self.command("configure", "openai", "offline-model", "--profile", "test",
                                                 "--context-window", "16000", "--max-output-tokens", "512"), 0)
        runtime = load_runtime_config()
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                                 'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = OpenAIResponsesClient(runtime.provider, http_client=http)
            events = [event async for event in client.stream("test", (Message("user", "hi"),), ())]
            self.assertTrue(any(event.kind is ModelEventKind.TEXT_DELTA for event in events))
            self.assertEqual(str(seen[0].url), "https://api.openai.com/v1/responses")
            self.assertEqual(seen[0].headers["Authorization"], "Bearer offline-secret")
            self.assertEqual(await self.command("logout", "openai"), 0)
            with self.assertRaises(ProviderError):
                _ = [event async for event in client.stream("test", (), ())]
            self.assertEqual(len(seen), 1)
            await client.aclose()
        self.assertNotIn("offline-secret", self.output.getvalue() + self.errors.getvalue())
        self.assertNotIn("offline-secret", (self.root / "config.toml").read_text())

    async def test_help_and_platform_list_do_not_require_model_configuration(self):
        self.assertEqual(await self.command("--help"), 0)
        self.assertEqual(await self.command("providers"), 0)
        self.assertIn("openai-codex", self.output.getvalue())
        self.assertFalse((self.root / "config.toml").exists())

    async def test_configure_keeps_both_auth_types_and_model_override_keeps_auth(self):
        store = CredentialStore()
        store.set("anthropic", Credential("oauth", "account-token"))
        store.set("anthropic", Credential("api_key", "api-token"))
        for kind in ("oauth", "api_key"):
            self.assertEqual(await self.command("configure", "anthropic", "offline-model", "--auth", kind,
                             "--profile", kind, "--context-window", "16000", "--max-output-tokens", "512"), 0)
        runtime = load_runtime_config(cli_profile="api_key")
        self.assertEqual(runtime.provider.auth_source.kind, "api_key")
        updated = replace_model(runtime.profiles[0], "new-model")
        self.assertEqual(updated.provider.auth_source, runtime.profiles[0].provider.auth_source)
        self.assertEqual(updated.provider.anthropic_messages_path, "/v1/messages")

    async def test_invalid_limits_fail_without_creating_config(self):
        CredentialStore().set("openai", Credential("api_key", "key"))
        self.assertEqual(await self.command("configure", "openai", "offline-model",
                         "--context-window", "0", "--max-output-tokens", "512"), 2)
        self.assertFalse((self.root / "config.toml").exists())
