from __future__ import annotations

import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from code_agent.config.loader import load_runtime_config
from code_agent.context.repo_index import RepoIndexService
from code_agent.core.attachments import AttachmentRef
from code_agent.interfaces.attachment_input import DEFAULT_ATTACHMENT_PROMPT
from code_agent.interfaces.commands import CommandKind
from code_agent.orchestration.models import AgentDefinition, AgentRole
from code_agent_win import agent_modes
from code_agent_win.app import Application, create_application
from code_agent_win.cli import (
    _split_attachment_options,
    _split_global_options,
    _split_mode_option,
    run,
)
from code_agent_win.rewind_sessions import CoordinatedSessionRepository
from tests.agent_app_test_support import _configured_application


class _CliIngestor:
    def ingest_paths(self, *_: object, **__: object):
        self.thread = threading.get_ident()
        return (AttachmentRef("a" * 64, "text/plain", 4, "note.txt"),)


class _CliApplication:
    def __init__(self) -> None:
        self.attachment_ingestor = _CliIngestor()
        self.tui = SimpleNamespace(
            attachment_draft=SimpleNamespace(validate=lambda _: None)
        )
        self.dispatcher = SimpleNamespace(interactive=True)
        self.controller = object()
        self.foreground_tasks = object()

    async def startup(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_aclose_closes_each_resource_once_in_order(self) -> None:
        calls: list[str] = []

        class AsyncCloser:
            def __init__(self, name: str) -> None:
                self.name = name

            async def aclose(self) -> None:
                calls.append(self.name)

        class Closer:
            def close(self) -> None:
                calls.append("workspace")

        subagents = AsyncCloser("subagents")
        model = AsyncCloser("model")
        mcp = AsyncCloser("mcp")
        application = Application(
            controller=object(), foreground_tasks=object(), tui=object(),
            dispatcher=object(), model=model, mcp=mcp, subagents=subagents,
            workspace_runtime=Closer(),
        )

        await application.aclose()
        await application.aclose()

        self.assertEqual(calls, ["subagents", "model", "mcp", "workspace"])
        self.assertEqual({name: calls.count(name) for name in calls}, {
            "subagents": 1, "model": 1, "mcp": 1, "workspace": 1,
        })

    async def test_aclose_releases_all_resources_after_first_error(self) -> None:
        subagents = SimpleNamespace(
            aclose=AsyncMock(side_effect=RuntimeError("subagent close failed"))
        )
        model = SimpleNamespace(aclose=AsyncMock())
        mcp = SimpleNamespace(aclose=AsyncMock())
        workspace_close = Mock()
        application = Application(
            controller=object(), foreground_tasks=object(), tui=object(),
            dispatcher=object(), model=model, mcp=mcp, subagents=subagents,
            workspace_runtime=SimpleNamespace(close=workspace_close),
        )

        with self.assertRaisesRegex(RuntimeError, "subagent close failed"):
            await application.aclose()

        model.aclose.assert_awaited_once()
        mcp.aclose.assert_awaited_once()
        workspace_close.assert_called_once()
        await application.aclose()
        workspace_close.assert_called_once()


class CliFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_global_options_are_removed_before_command_parsing(self) -> None:
        profile, model, command = _split_global_options(
            ("--profile", "company", "ask", "inspect", "--model", "fast")
        )

        self.assertEqual(
            (profile, model, command),
            ("company", "fast", ("ask", "inspect")),
        )
        with self.assertRaises(ValueError):
            _split_global_options(
                ("--model", "fast", "--model", "slow", "ask", "inspect")
            )

        mode, remaining = _split_mode_option(
            ("ask", "inspect", "--mode", "ultra")
        )
        self.assertEqual((mode, remaining), ("ultra", ("ask", "inspect")))
        with self.assertRaises(ValueError):
            _split_mode_option(("--mode", "unbounded", "ask", "inspect"))

        paths, remaining = _split_attachment_options(
            ("ask", "inspect", "--attach", r"C:\shots\error.png")
        )
        self.assertEqual(paths, (r"C:\shots\error.png",))
        self.assertEqual(remaining, ("ask", "inspect"))

    async def test_cli_reports_safe_error_without_a_traceback(self) -> None:
        stderr = StringIO()

        with patch(
            "code_agent_win.cli.create_application",
            side_effect=RuntimeError("secret detail"),
        ):
            with patch("sys.stderr", stderr):
                status = await run(("ask", "inspect"))

        self.assertEqual(status, 1)
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("secret detail", stderr.getvalue())

    async def test_attachment_only_commands_use_default_prompt_off_thread(self) -> None:
        cases = (
            (("ask", "--attach", "C:\\note.txt"), CommandKind.ASK, None),
            (
                ("run", "--json", "--attach", "C:\\note.txt"),
                CommandKind.RUN_JSON,
                None,
            ),
            (
                ("resume", "thread-1", "--attach", "C:\\note.txt"),
                CommandKind.RESUME,
                "thread-1",
            ),
        )
        for arguments, kind, thread_id in cases:
            with self.subTest(kind=kind):
                application = _CliApplication()
                execute = AsyncMock(return_value=0)
                caller = threading.get_ident()
                with patch(
                    "code_agent_win.cli.create_application",
                    return_value=application,
                ), patch("code_agent_win.cli.execute_command", execute):
                    self.assertEqual(await run(arguments), 0)

                command = execute.await_args.args[0]
                self.assertEqual(command.kind, kind)
                self.assertEqual(command.prompt, DEFAULT_ATTACHMENT_PROMPT)
                self.assertEqual(command.thread_id, thread_id)
                self.assertNotEqual(application.attachment_ingestor.thread, caller)
                self.assertEqual(len(execute.await_args.kwargs["attachments"]), 1)

    async def test_attachment_is_rejected_before_unsupported_command_ingestion(self) -> None:
        stderr = StringIO()
        with patch("code_agent_win.cli.create_application") as create:
            with patch("sys.stderr", stderr):
                status = await run(
                    ("task", "list", "--attach", r"C:\workspace\note.txt")
                )

        self.assertEqual(status, 2)
        create.assert_not_called()
        self.assertIn("not supported", stderr.getvalue())


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

    def test_application_uses_coordinated_sessions_for_engine_and_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application, _, _ = _configured_application(Path(temporary).resolve())
        sessions = application.tui.sessions
        self.assertIsInstance(sessions, CoordinatedSessionRepository)
        self.assertIs(application.foreground_tasks._sessions, sessions)
        self.assertIs(application.controller._engine._journal._repository, sessions)
        self.assertIs(application.tui.history, sessions)
        self.assertIs(application.tui.evidence, sessions)


if __name__ == "__main__":
    unittest.main()
