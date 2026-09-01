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


class ApplicationPathTests(unittest.TestCase):
    def test_managed_worktrees_are_outside_protected_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local_app_data = Path(temporary).resolve()
            with patch.dict(
                "os.environ", {"LOCALAPPDATA": str(local_app_data)}, clear=False
            ):
                storage = _workspace_storage_path()

            self.assertEqual(storage, local_app_data / "chaos-agent-workspaces")
            self.assertNotEqual(storage.parent, local_app_data / "chaos-agent")

class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_does_not_register_peer_until_tui_runs(self) -> None:
        workspace = SimpleNamespace(startup=AsyncMock(), close=Mock())
        peers = SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())
        application = Application(
            controller=object(),
            foreground_tasks=object(),
            tui=object(),
            dispatcher=object(),
            model=object(),
            workspace_runtime=workspace,
            peers=peers,
        )

        await application.startup()

        workspace.startup.assert_awaited_once()
        peers.start.assert_not_awaited()
        await application.aclose()

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

if __name__ == "__main__":
    unittest.main()
