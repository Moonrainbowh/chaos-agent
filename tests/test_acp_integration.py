from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from code_agent.acp import ChaosAcpAgent
from code_agent_win.acp_adapter import serve_acp
from code_agent_win.cli import _ACP_HELP, _HELP, run


class FakeApplication:
    def __init__(self, root: Path) -> None:
        self.controller = SimpleNamespace(ask=lambda *args, **kwargs: None)
        self.sessions = SimpleNamespace(
            create_thread=lambda: None,
            load_messages=lambda _: None,
        )
        self.workspace_root = root
        self.dispatcher = SimpleNamespace(interactive=True)
        self.startup = AsyncMock()
        self.aclose = AsyncMock()


class AcpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_serve_acp_composes_application_into_official_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = FakeApplication(Path(temporary).resolve())
            with patch(
                "code_agent_win.acp_adapter.run_agent", new=AsyncMock()
            ) as run_agent:
                await serve_acp(application)

        self.assertFalse(application.dispatcher.interactive)
        adapted = run_agent.await_args.args[0]
        self.assertIsInstance(adapted, ChaosAcpAgent)
        self.assertIs(adapted._controller, application.controller)
        self.assertIs(adapted._sessions, application.sessions)

    async def test_cli_acp_owns_lifecycle_without_command_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = FakeApplication(Path(temporary).resolve())
            serve = AsyncMock()
            with patch(
                "code_agent_win.cli.create_application", return_value=application
            ), patch("code_agent_win.cli.serve_acp", serve):
                status = await run(("acp", "--profile", "fast"))

        self.assertEqual(status, 0)
        application.startup.assert_awaited_once()
        serve.assert_awaited_once_with(application)
        application.aclose.assert_awaited_once()

    async def test_acp_rejects_positional_arguments_before_application(self) -> None:
        with patch("code_agent_win.cli.create_application") as create:
            status = await run(("acp", "unexpected"))

        self.assertEqual(status, 2)
        create.assert_not_called()

    def test_help_lists_acp_command(self) -> None:
        self.assertIn("acp", _HELP)
        self.assertIn("Serve ACP v1", _HELP)
        self.assertIn("stdio", _ACP_HELP)


if __name__ == "__main__":
    unittest.main()
