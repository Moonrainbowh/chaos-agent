from __future__ import annotations

import asyncio
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from chaos_agent.cli import run
from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote_cli import _parse, serve_host


class HostCliTests(unittest.IsolatedAsyncioTestCase):
    def test_bind_options_are_explicit_and_validated(self):
        self.assertEqual(_parse(()), (None, 8787))
        self.assertEqual(_parse(("--lan",)), ("0.0.0.0", 8787))
        self.assertEqual(_parse(("--bind", "192.168.1.10", "--port", "9000")), ("192.168.1.10", 9000))
        for arguments in (("--bind",), ("--port", "0"), ("--port", "65536"), ("--port", "abc"), ("--lan", "--bind", "127.0.0.1"), ("--lan", "--lan"), ("--port", "8787", "--port", "8788")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                _parse(arguments)

    async def test_default_bind_is_localhost_without_network_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            pairing = PairingStore(Path(directory) / "devices.json")
            server = SimpleNamespace(serve=AsyncMock())
            with patch("chaos_agent.remote_cli.create_host_app", return_value=(object(), pairing)), patch("uvicorn.Config") as config, patch("uvicorn.Server", return_value=server), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(await serve_host(object(), ()), 0)
            self.assertEqual(config.call_args.kwargs["host"], "127.0.0.1")
            server.serve.assert_awaited_once()

    async def test_revoke_cli_does_not_initialize_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            with patch("chaos_agent.remote_cli.PairingStore", return_value=PairingStore(path)), patch("chaos_agent.cli.create_application") as create, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(await run(("host", "revoke-device")), 0)
            create.assert_not_called()
            self.assertFalse(store.authenticate(credential))

    def test_real_revoke_subprocess_invalidates_running_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chaos-agent" / "remote" / "devices.json"
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            env = {**os.environ, "LOCALAPPDATA": directory, "CHAOS_DEBUG_TRACE": "0"}
            result = subprocess.run([sys.executable, "-m", "chaos_agent.cli", "host", "revoke-device"], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("device revoked", result.stdout)
            self.assertFalse(store.authenticate(credential))


if __name__ == "__main__":
    unittest.main()
