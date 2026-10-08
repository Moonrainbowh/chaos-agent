"""Production Host tool disclosures fit the authorized default prompt limits."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_agent.capabilities.catalog import contract_result, disclosed_contract, tool_definition_digest
from code_agent.config.loader import RuntimeConfig
from code_agent.context.errors import PromptBudgetError
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ActionRequest, Message
from code_agent.core.task_state import TaskState
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from chaos_agent.app import create_application
from chaos_agent.application_context import _profile_prompt_budget


class _ForbiddenProvider:
    def __init__(self):
        self.calls = []

    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("budget regression must not call a Provider")


@contextmanager
def _application(strategy, *, git, tool_ceiling=None):
    policy = None if strategy == "semantic" else WindowPolicy(
        strategy=strategy, work_tokens=65_536, safety_tokens=8_000,
        handoff_tokens=4_096, task_tokens=50_000,
    )
    profile = ModelProfile(
        "budget", ProviderConfig("https://api.example.test", "glm-5.3-flash",
                                 ApiProtocol.CHAT_COMPLETIONS, api_key_env="UNUSED_KEY"),
        1_000_000, 4_096, context_policy=policy,
    )
    model = _ForbiddenProvider()
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        container = Path(temporary).resolve()
        root, state = container / "workspace", container / "state"
        root.mkdir()
        state.mkdir()
        if git:
            executable = shutil.which("git")
            if executable is None:
                raise unittest.SkipTest("Git is needed for the actual Git Host catalogue")
            subprocess.run([executable, "init", "--quiet", str(root)],
                           check=True, capture_output=True, timeout=30)
        (root / "AGENTS.md").write_text("Keep the source contract unchanged.\n", encoding="utf-8")
        (root / "names.py").write_text("def clean_names(values):\n    return list(values)\n", encoding="utf-8")
        config = RuntimeConfig(
            provider=profile.provider, profile=profile.name,
            approval_mode=ApprovalMode.AUTO, allow_sensitive_paths=False,
            config_path=container / "provider-config" / "config.toml", profiles=(profile,),
        )
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("CHAOS_", "CODE_AGENT_"))}
        environment.update(HOME=str(container / "home"), USERPROFILE=str(container / "home"),
                           LOCALAPPDATA=str(state), XDG_CONFIG_HOME=str(container / "config"),
                           CHAOS_DEBUG_TRACE="0")
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for name, value in (
            ("_session_path", state / "sessions.sqlite3"),
            ("_product_state_root", state),
            ("_workspace_storage_path", container / "managed-workspaces"),
            ("load_runtime_config", config),
        ):
            stack.enter_context(patch("chaos_agent.app." + name, return_value=value))
        stack.enter_context(patch("chaos_agent.app._model_client", return_value=model))
        if tool_ceiling is not None:
            stack.enter_context(patch(
                "chaos_agent.application_context._profile_prompt_budget",
                side_effect=lambda current, mode=None: replace(
                    _profile_prompt_budget(current, mode), max_tool_tokens=tool_ceiling),
            ))
        app = create_application(root, mode_name="medium")
        yield app, model


async def _request(app):
    thread = await app.sessions.create_thread()
    message = Message("user", "Analyze names.py. Read only; no changes.")
    await app.sessions.append_message(thread, message)
    return ContextRequest(thread, 1, (message,), "", (), TaskState.empty(), CancellationToken())


class BuiltinToolPromptBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_host_initial_and_full_disclosures_fit_default_budget(self):
        for strategy in ("semantic", "summary", "boundary", "persistent"):
            for topology in ("single", "team"):
                for git in (False, True):
                    with self.subTest(strategy=strategy, topology=topology, git=git):
                        with _application(strategy, git=git) as (app, model):
                            try:
                                await app.runtime_selection.use(topology=topology, idle=True)
                                engine = app.controller._engine
                                request = await _request(app)
                                catalogue = tuple(engine._actions.tools())
                                disclosures = {tool.name: tool_definition_digest(tool)
                                               for tool in catalogue if tool.name != "load_tool_contract"}
                                for label, loaded in (("initial", {}), ("full", disclosures)):
                                    with self.subTest(disclosure=label):
                                        tools, _ = engine._advertised_tools(None, loaded, intent="analyze")
                                        bundle = await engine._context.build(replace(request, tools=tools))
                                        metrics = bundle.measurements
                                        expected_ceiling = 300_000 if strategy == "semantic" else 65_536
                                        self.assertEqual(metrics["prompt_budget_tokens"], expected_ceiling)
                                        self.assertLessEqual(metrics["tool_tokens"], 20_000)
                                        self.assertLessEqual(metrics["prompt_estimated_tokens"],
                                                             expected_ceiling - metrics["prompt_safety_tokens"])
                                        self.assertGreaterEqual(metrics["message_tokens"], 2_000)
                                self.assertEqual(model.calls, [])
                            finally:
                                await app.aclose()

    async def test_explicit_2000_still_rejects_default_git_initial_catalogue(self):
        with _application("semantic", git=True, tool_ceiling=2_000) as (app, model):
            try:
                engine = app.controller._engine
                request = await _request(app)
                tools, _ = engine._advertised_tools(None, {}, intent="analyze")
                with self.assertRaisesRegex(PromptBudgetError, "tool_tokens exceeds"):
                    await engine._context.build(replace(request, tools=tools))
                self.assertEqual(model.calls, [])
            finally:
                await app.aclose()

    async def test_explicit_2000_rejects_one_legitimate_non_git_disclosure(self):
        with _application("semantic", git=False, tool_ceiling=2_000) as (app, model):
            try:
                engine = app.controller._engine
                request = await _request(app)
                initial, _ = engine._advertised_tools(None, {}, intent="analyze")
                await engine._context.build(replace(request, tools=initial))
                result = contract_result(ActionRequest("load-peer", "load_tool_contract",
                                                       {"name": "list_agents"}), engine._actions.tools())
                self.assertFalse(result.is_error)
                name, digest = disclosed_contract(result)
                tools, _ = engine._advertised_tools(None, {name: digest}, intent="analyze")
                self.assertIn("list_agents", {tool.name for tool in tools})
                with self.assertRaisesRegex(PromptBudgetError, "tool_tokens exceeds"):
                    await engine._context.build(replace(request, tools=tools))
                self.assertEqual(model.calls, [])
            finally:
                await app.aclose()


if __name__ == "__main__":
    unittest.main()
