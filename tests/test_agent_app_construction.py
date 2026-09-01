from __future__ import annotations

import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from code_agent.config.loader import LocalConfigError, load_runtime_config
from code_agent.context.repo_index import RepoIndexService
from code_agent.core.attachments import AttachmentRef
from code_agent.interfaces.attachment_input import DEFAULT_ATTACHMENT_PROMPT
from code_agent.interfaces.commands import CommandKind
from code_agent.orchestration.models import AgentDefinition, AgentRole
from code_agent.plugins.models import PluginRisk
from code_agent_win import agent_modes
from code_agent_win.app import Application, _workspace_storage_path, create_application
from code_agent_win.cli import (
    _split_attachment_options,
    _split_global_options,
    _split_mode_option,
    run,
)
from code_agent_win.workspace_session_router import WorkspaceSessionRouter
from code_agent_win.runtime_support import host_risks
from tests.agent_app_test_support import _configured_application


class ApplicationConstructionTests(unittest.TestCase):
    def test_all_modes_share_complete_tools_and_plugin_contributions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = load_runtime_config(
                env={
                    "CHAOS_CONFIG": str(Path(temporary) / "missing.toml"),
                    "CHAOS_API": "responses",
                    "CHAOS_BASE_URL": "https://api.example.test",
                    "CHAOS_MODEL": "default-model",
                    "CHAOS_API_KEY_ENV": "KEY",
                }
            )
        profiles = {runtime.profile: runtime.profiles[0]}

        mode_env = {
            f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
            for mode in agent_modes.AgentMode
        }
        with patch.dict("os.environ", mode_env):
            registry, _ = agent_modes.build_mode_registry(profiles, runtime.profile)
        definitions = registry.definitions()

        self.assertEqual(
            {definition.tool_names for definition in definitions},
            {agent_modes.ALL_TOOLS},
        )
        self.assertIn("plan_workspace_edits_v1", agent_modes.READ_TOOLS)
        self.assertIn("apply_workspace_edit_plan_v1", agent_modes.TYPED_WRITE_TOOLS)
        self.assertEqual(host_risks()["plan_workspace_edits_v1"], PluginRisk.READ)
        self.assertEqual(host_risks()["apply_workspace_edit_plan_v1"], PluginRisk.WRITE)
        main_tools_for_mode = getattr(agent_modes, "main_tools_for_mode", None)
        self.assertIsNotNone(main_tools_for_mode)
        for definition in definitions:
            self.assertEqual(
                main_tools_for_mode(definition.tool_names, ("plugin.inspect",)),
                definition.tool_names + ("plugin.inspect",),
            )

    def test_tui_uses_the_session_repository_for_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(root / "missing.toml"), "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://api.example.test", "CHAOS_MODEL": "test",
                "CHAOS_API_KEY_ENV": "KEY",
            })
            with patch("code_agent_win.app._model_client", return_value=object()):
                with patch(
                    "code_agent_win.app._session_path",
                    return_value=root / "sessions.sqlite3",
                ):
                    with patch("code_agent_win.app.load_runtime_config", return_value=runtime):
                        mode_env = {
                            f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
                            for mode in agent_modes.AgentMode
                        }
                        with patch.dict("os.environ", mode_env):
                            application = create_application(root)
                            main_context = application.controller._engine._context._inner
                            child_agent = AgentDefinition(
                                "shared-index-child",
                                AgentRole.SEARCH,
                                application.mode,
                                "Inspect the repository.",
                                ("read_file",),
                            )
                            child_engine, _ = application.subagents._runner._factory(
                                child_agent
                            )
                            child_context = child_engine._context._inner

        self.assertIs(application.tui.sessions, application.tui.history)
        self.assertIs(application.tui.evidence, application.tui.sessions)
        self.assertEqual(application.mode.definition.mode.value, "medium")
        self.assertEqual(application.mode.model, "test")
        self.assertIsNotNone(application.plugins)
        self.assertIsNotNone(application.subagents)
        self.assertIsInstance(application.repo_index, RepoIndexService)
        self.assertIs(main_context.repo_map.index, application.repo_index)
        self.assertIs(child_context.repo_map.index, application.repo_index)

    def test_application_uses_workspace_routed_sessions_for_engine_and_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        sessions = application.tui.sessions
        self.assertIsInstance(sessions, WorkspaceSessionRouter)
        self.assertIs(application.foreground_tasks._sessions, sessions)
        self.assertIs(application.controller._engine._journal._repository, sessions)
        self.assertIs(application.tui.history, sessions)
        self.assertIs(application.tui.evidence, sessions)

if __name__ == "__main__":
    unittest.main()
