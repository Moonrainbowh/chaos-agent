"""S11 real product entry, default semantic request and restart isolation."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from chaos_agent.app import create_application
from code_agent.config.loader import load_runtime_config
from code_agent.providers.config import ConfiguredApiKey
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.sessions.errors import SessionNotFound


class ProjectMemoryApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Memory inclusion and restart are tested at the original explicit cap.
        scope = patch.dict(os.environ, {"CHAOS_MAX_PROMPT_TOKENS": "20000"})
        scope.start()
        self.addCleanup(scope.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.container = Path(self.directory.name).resolve()
        self.state = self.container / "explicit-state"
        self.database = self.state / "sessions.sqlite3"
        assert self.database.resolve().is_relative_to(self.container)
        self.requests = []

    def application(self, name="alpha"):
        root = self.container / name
        root.mkdir(exist_ok=True)
        runtime = load_runtime_config(env={
            "CHAOS_CONFIG": str(self.container / "missing.toml"),
            "CHAOS_API": "responses", "CHAOS_MODEL": "gpt-4.1",
            "CHAOS_BASE_URL": "https://offline.example.test", "CHAOS_API_KEY_ENV": "KEY",
            "CHAOS_APPROVAL_MODE": "full-local",
        })
        runtime = replace(runtime, profiles=tuple(replace(profile,
            provider=replace(profile.provider, api_key_env=None,
                api_key_source=ConfiguredApiKey("offline-fixture-key")))
            for profile in runtime.profiles))

        def respond(request):
            self.requests.append(json.loads(request.content))
            events = [
                {"type": "response.output_text.delta", "delta": "No workspace changes requested."},
                {"type": "response.completed", "response": {
                    "usage": {"input_tokens": 30, "output_tokens": 5}}},
            ]
            return httpx.Response(200, content="".join(
                "data: " + json.dumps(event) + "\n\n" for event in events))

        clients = []
        def model(provider):
            http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            clients.append(http)
            return OpenAIResponsesClient(provider, http_client=http)

        # All paths are checked before the application constructs any Repository.
        assert self.database.resolve().parent == self.state.resolve()
        with patch.dict("os.environ", {
            "USERPROFILE": str(self.container / "profile"),
            "LOCALAPPDATA": str(self.container / "local"),
            "CHAOS_WORKSPACE_MODE": "direct",
        }), patch("chaos_agent.app._session_path", return_value=self.database), \
             patch("chaos_agent.app._product_state_root", return_value=self.state), \
             patch("chaos_agent.app._workspace_storage_path", return_value=self.state / "workspaces"), \
             patch("chaos_agent.app.load_runtime_config", return_value=runtime), \
             patch("chaos_agent.app._model_client", side_effect=model):
            app = create_application(root)
        app.tui._write = lambda value: None
        for http in clients:
            self.addAsyncCleanup(http.aclose)
        self.addAsyncCleanup(app.aclose)
        return app

    async def ask(self, app, text):
        before = len(self.requests)
        self.assertTrue(await app.tui.submit(text))
        await app.tui.wait_idle()
        self.assertGreater(len(self.requests), before)
        return self.requests[before:]

    async def test_user_save_new_conversation_restart_and_other_project(self):
        app = self.application()
        self.assertIs(app.project_memory, app.tui.project_memory)
        self.assertTrue(await app.tui.submit("/memory save 盐穴建模采用地质分层 S11_DECISION"))
        self.assertEqual(self.requests, [])
        item, = await app.project_memory.list()
        self.assertEqual(item.origin, "user_explicit")
        self.assertEqual(item.source_refs["source_root"], str(app.workspace_root))
        sent = await self.ask(app, "请说明盐穴建模选择")
        self.assertIn("UNTRUSTED_PROJECT_MEMORY", sent[0]["instructions"])
        self.assertIn("S11_DECISION", sent[0]["instructions"])
        self.assertEqual(app.controller._engine._model.constraints.host_prompt_tokens, 20000)
        thread = app.tui.current_thread_id
        await app.aclose()

        restarted = self.application()
        sent = await self.ask(restarted, "新对话：盐穴建模选择是什么？")
        self.assertIn("S11_DECISION", sent[0]["instructions"])
        self.assertNotEqual(restarted.tui.current_thread_id, thread)
        other = self.application("beta")
        sent = await self.ask(other, "盐穴建模选择是什么？")
        self.assertNotIn("S11_DECISION", sent[0]["instructions"])
        self.assertEqual(await other.project_memory.list(), ())
        with self.assertRaises(SessionNotFound):
            await other.project_memory.show(item.memory_id)
        with self.assertRaises(PermissionError):
            await other.project_memory.list(thread_id=thread)

    async def test_revision_conditions_withdraw_and_forget_never_reactivate(self):
        app = self.application()
        await app.tui.submit("/memory save SQLite S11_OLD")
        item, = await app.project_memory.list()
        await app.tui.submit(f"/memory revise {item.memory_id} SQLite S11_NEW")
        revised = await app.project_memory.show(item.memory_id)
        self.assertEqual(revised.revision, 2)
        sent = await self.ask(app, "SQLite decision")
        self.assertIn("S11_NEW", sent[0]["instructions"])
        self.assertNotIn("S11_OLD", sent[0]["instructions"])
        await app.tui.submit(f"/memory withdraw {item.memory_id}")
        sent = await self.ask(app, "SQLite decision")
        self.assertNotIn("S11_NEW", sent[0]["instructions"])

        # Unknown conditions cannot become applicable through old related messages.
        await app.tui.submit('/memory save --conditions {"unknown":"x"} -- SQLite S11_UNKNOWN')
        sent = await self.ask(app, "SQLite decision unknown x")
        self.assertNotIn("S11_UNKNOWN", sent[0]["instructions"])
        await app.tui.submit(f"/memory delete {item.memory_id}")
        with self.assertRaises(SessionNotFound):
            await app.project_memory.show(item.memory_id)
        with self.assertRaises(ValueError):
            await app.project_memory.save("SQLite S11_OLD")
        with self.assertRaises(ValueError):
            await app.project_memory.save("SQLite S11_NEW")
        sent = await self.ask(app, "SQLite decision")
        self.assertNotIn("S11_NEW", sent[0]["instructions"])


    async def test_fresh_user_shell_thread_can_save_without_model_or_scope_relaxation(self):
        app = self.application()
        command = "Write-Output S11_LOCAL_THREAD" if os.name == "nt" else "printf S11_LOCAL_THREAD"
        self.assertTrue(await app.tui.submit("!" + command))
        await app.tui.wait_idle()
        thread = app.tui.current_thread_id
        self.assertIsNotNone(thread)
        self.assertIsNone(await app.sessions.load_task_for_thread(thread))
        self.assertEqual(self.requests, [])
        self.assertTrue(await app.tui.submit("/memory save SQLite S11_AFTER_SHELL"))
        item, = await app.project_memory.list(thread_id=thread)
        self.assertEqual(item.source_refs["explicit_user_thread"], thread)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
