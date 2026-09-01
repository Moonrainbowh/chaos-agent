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
from code_agent_win import agent_modes
from code_agent_win.app import Application, _workspace_storage_path, create_application
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

class CliFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_and_version_do_not_require_provider_configuration(self) -> None:
        stdout = StringIO()

        with patch("code_agent_win.cli.create_application") as create, patch(
            "code_agent_win.cli.package_version", return_value="1.2.3"
        ), patch("sys.stdout", stdout):
            self.assertEqual(await run(("--help",)), 0)
            self.assertEqual(await run(("--version",)), 0)

        create.assert_not_called()
        self.assertIn("Usage: chaos-agent", stdout.getvalue())
        self.assertIn("chaos-agent 1.2.3", stdout.getvalue())

    async def test_configuration_error_explains_the_next_action(self) -> None:
        stderr = StringIO()

        with patch(
            "code_agent_win.cli.create_application",
            side_effect=LocalConfigError("base_url must be non-empty text"),
        ), patch("sys.stderr", stderr):
            status = await run(("task", "list"))

        self.assertEqual(status, 2)
        self.assertIn(
            "configuration error: base_url must be non-empty text", stderr.getvalue()
        )
        self.assertIn("config.toml", stderr.getvalue())
        self.assertIn("CHAOS_*", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

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

if __name__ == "__main__":
    unittest.main()
