from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from acp import PROTOCOL_VERSION, spawn_agent_process


class SmokeClient:
    def on_connect(self, connection: object) -> None:
        self.connection = connection

    async def session_update(self, session_id: str, update: object) -> None:
        del session_id, update

    async def ext_method(
        self, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        del method, params
        return {}

    async def ext_notification(
        self, method: str, params: dict[str, object]
    ) -> None:
        del method, params


class AcpStdioSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_can_initialize_and_roundtrip_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            workspace = base / "workspace"
            local_state = base / "local-state"
            workspace.mkdir()
            local_state.mkdir()
            environment = dict(os.environ)
            environment.update(
                {
                    "LOCALAPPDATA": str(local_state),
                    "CHAOS_CONFIG": str(base / "missing.toml"),
                    "CHAOS_API": "responses",
                    "CHAOS_BASE_URL": "https://api.example.test",
                    "CHAOS_MODEL": "acp-smoke",
                    "CHAOS_API_KEY_ENV": "ACP_SMOKE_KEY",
                    "ACP_SMOKE_KEY": "test-only-key",
                }
            )
            async with spawn_agent_process(
                SmokeClient(),
                sys.executable,
                "-m",
                "code_agent_win.acp_cli",
                env=environment,
                cwd=workspace,
            ) as (connection, process):
                initialized = await connection.initialize(PROTOCOL_VERSION)
                created = await connection.new_session(str(workspace))
                listed = await connection.list_sessions(str(workspace))
                loaded = await connection.load_session(
                    str(workspace), created.session_id
                )

                self.assertEqual(initialized.protocol_version, PROTOCOL_VERSION)
                self.assertIn(
                    created.session_id,
                    {session.session_id for session in listed.sessions},
                )
                self.assertIsNotNone(loaded)
                self.assertIsNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
