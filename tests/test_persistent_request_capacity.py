"""Persistent production composition must select against its actual request guard."""
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from chaos_agent.application_context import RuntimeContextFactory
from chaos_agent.runtime_extensions import ThreadRuntimeBinding
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import Message
from code_agent.core.task_state import TaskState
from code_agent.providers.config import ConfiguredApiKey
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from tests.test_prepared_context_assembly import Skills
from tests.test_thread_intelligence_runtime import _profile, _mode


class PersistentRequestCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, long_history=False):
        # This fixture isolates Host ceiling admission from unrelated default
        # prose growth. The full Host prompt can legitimately exceed this cap.
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CHAOS_MAX_PROMPT_TOKENS": "8000"}), patch(
            "chaos_agent.application_context.windows_system_prompt",
            return_value="CAPACITY_HOST_FIXTURE Preserve all required user constraints.",
        ):
            root = Path(directory)
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            self.addCleanup(sessions.close)
            thread = await sessions.create_thread()
            user = Message("user", "Inspect the current source; preserve the constraints.")
            await sessions.append_message(thread, user)
            if long_history:
                await sessions.append_message(thread, Message("assistant", "old evidence " * 5000))
            before = await sessions.load_messages(thread)
            profile = replace(_profile(), context_window=1000000,
                provider=replace(_profile().provider, api_key_env=None, api_key_source=ConfiguredApiKey("offline-key")),
                context_policy=WindowPolicy(strategy="persistent", work_tokens=1000000, safety_tokens=100))
            calls = []
            def forbidden(request):
                calls.append(request)
                raise AssertionError("HTTP is forbidden in this regression")
            async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
                model = OpenAIResponsesClient(profile.provider, http_client=http, max_output_tokens=profile.max_output_tokens)
                guard = WorkspacePathGuard(root)
                files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
                factory = RuntimeContextFactory(root, git_available=False, repo_map_enabled=False,
                    guard=guard, files=files, repo_index=None, repo_view_cache=None,
                    sessions=sessions, thread_binding=ThreadRuntimeBinding(), skills=Skills(""))
                assembly = factory(_mode(profile), model, profile)
                bundle = await assembly.build(ContextRequest(thread, 1, (user,), "", (), TaskState(), CancellationToken()))
                self.assertIn('CAPACITY_HOST_FIXTURE', bundle.system_prompt)
                cap = assembly.model_client.effective_input_cap()
                self.assertEqual(cap, 7900)
                self.assertEqual(bundle.measurements['window_input_cap'], cap)
                self.assertEqual(bundle.measurements['prompt_budget_tokens'], cap)
                self.assertEqual(assembly.model_client.request_estimate_kind, 'prepared-json-v1 local estimate')
                await assembly.model_client.preflight_request(bundle.system_prompt, bundle.messages, ())
                self.assertEqual(calls, [])
                self.assertEqual(await sessions.load_messages(thread), before)
                windows = await sessions.context_records(thread, 'window')
                usage = await sessions.context_records(thread, 'usage')
                self.assertEqual(usage, ())
                if long_history:
                    self.assertEqual(len(windows), 1)
                    self.assertEqual(windows[0]['reason'], 'capacity_fallback')
                    self.assertEqual(windows[0]['carry'], '')
                    self.assertEqual(bundle.measurements['window_number'], 1)
                    self.assertNotIn('old evidence', str(bundle.messages))
                else:
                    self.assertEqual(windows, ())
                # A reset may remove old history, but must never make an
                # oversized current user request fit by silently dropping it.
                required = Message('user', 'REQUIRED_STATE_MARKER ' * 500)
                await sessions.append_message(thread, required)
                required_history = await sessions.load_messages(thread)
                with self.assertRaisesRegex(ValueError, 'final input'):
                    await assembly.model_client.preflight_request(bundle.system_prompt, (required,), ())
                with self.assertRaisesRegex(ValueError, 'input cannot fit'):
                    await assembly.build(ContextRequest(thread, 2, (required,), '', (), TaskState(), CancellationToken()))
                self.assertEqual(calls, [])
                self.assertEqual(await sessions.load_messages(thread), required_history)
                self.assertEqual(await sessions.context_records(thread, 'window'), windows)
                self.assertEqual(await sessions.context_records(thread, 'usage'), ())

    async def test_frozen_host_cap_is_the_persistent_threshold(self):
        await self.exercise()

    async def test_host_capacity_resets_without_summary_or_history_mutation(self):
        await self.exercise(long_history=True)
