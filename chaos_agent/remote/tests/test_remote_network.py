from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote.server import create_host_app
from code_agent.core.events import AgentEvent, EventKind
from code_agent.project_launcher.store import ProjectStore


class _Foreground:
    async def start(self, prompt):
        return SimpleNamespace(id="task", thread_id="thread")

    async def events(self, task_id):
        yield AgentEvent(EventKind.COMPLETED)


@contextlib.contextmanager
def _host(path, bind):
    pairing = PairingStore(path)
    app, _ = create_host_app(SimpleNamespace(foreground_tasks=_Foreground()), pairing=pairing,
                             project_store=ProjectStore(Path(path).resolve().with_name("projects.json")))
    listener = socket.socket()
    listener.bind((bind, 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("test Host did not start")
            time.sleep(0.02)
        yield pairing, port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("test Host did not stop")


class NetworkHostTests(unittest.TestCase):
    def _exercise(self, bind, address):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            with _host(path, bind) as (pairing, port), httpx.Client(trust_env=False, timeout=5) as client:
                url = f"http://{address}:{port}"
                self.assertEqual(client.get(url).status_code, 200)
                self.assertEqual(client.get(url + "/status").status_code, 401)
                credential = client.post(url + "/pair", json={"token": pairing.issue_token()}).json()["device_credential"]
                headers = {"authorization": f"Bearer {credential}"}
                client.post(url + "/sessions/current/messages", headers=headers, json={"prompt": "offline test"}).raise_for_status()
                with connect(f"ws://{address}:{port}/sessions/current/events", open_timeout=5) as websocket:
                    websocket.send("auth:" + credential)
                    self.assertEqual(json.loads(websocket.recv(timeout=5))["event"], "task_started")
                    self.assertEqual(json.loads(websocket.recv(timeout=5))["event"], "task_completed")
                with connect(f"ws://{address}:{port}/sessions/current/events", open_timeout=5) as websocket:
                    websocket.send("auth:wrong")
                    with self.assertRaises(ConnectionClosed) as rejected:
                        websocket.recv(timeout=5)
                    self.assertEqual(rejected.exception.rcvd.code, 4401)
            with _host(path, bind) as (_, port), httpx.Client(trust_env=False, timeout=5) as client:
                url = f"http://{address}:{port}/status"
                self.assertEqual(client.get(url, headers=headers).status_code, 200)
                PairingStore(path).revoke()
                self.assertEqual(client.get(url, headers=headers).status_code, 401)

    def test_real_tcp_http_websocket_and_host_restart(self):
        self._exercise("127.0.0.1", "127.0.0.1")

    @unittest.skipUnless(os.getenv("CHAOS_TEST_LAN_ADDRESS"), "explicit LAN interface required")
    def test_explicit_lan_interface(self):
        self._exercise("0.0.0.0", os.environ["CHAOS_TEST_LAN_ADDRESS"])
