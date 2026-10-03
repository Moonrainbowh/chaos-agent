from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind

from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote.protocol import event_from_agent
from chaos_agent.remote.server import create_host_app
from chaos_agent.remote.task_controller import RemoteTaskController


class PairingTests(unittest.TestCase):
    def test_token_is_single_use_and_credential_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PairingStore(Path(directory) / "devices.json")
            token = store.issue_token()
            credential = store.pair(token)

            self.assertTrue(store.authenticate(credential))
            self.assertFalse(store.authenticate(token))
            with self.assertRaises(PermissionError):
                store.pair(token)

    def test_reissue_revokes_previous_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PairingStore(Path(directory) / "devices.json")
            first = store.pair(store.issue_token())
            second = store.pair(store.issue_token())

            self.assertFalse(store.authenticate(first))
            self.assertTrue(store.authenticate(second))

    def test_credential_survives_store_recreation_and_is_not_stored_in_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            first = PairingStore(path)
            credential = first.pair(first.issue_token())

            restored = PairingStore(path)

            self.assertTrue(restored.authenticate(credential))
            self.assertNotIn(credential, path.read_text(encoding="utf-8"))
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["device_digest"]), 64)

    def test_revoke_is_persistent_and_immediately_invalidates_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            store = PairingStore(path)
            credential = store.pair(store.issue_token())

            store.revoke()

            self.assertFalse(store.authenticate(credential))
            self.assertFalse(PairingStore(path).authenticate(credential))

    def test_external_revoke_invalidates_running_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            host = PairingStore(path)
            credential = host.pair(host.issue_token())
            PairingStore(path).revoke()
            self.assertFalse(host.authenticate(credential))

    def test_issue_token_preserves_existing_device_and_replaces_old_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            old_token = store.issue_token()
            store.issue_token()
            self.assertTrue(PairingStore(path).authenticate(credential))
            with self.assertRaises(PermissionError):
                store.pair(old_token)

    def test_corrupt_deleted_or_unknown_version_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            document = json.loads(path.read_text())
            document["version"] = 999
            for contents in ("broken", json.dumps(document), '{"version":1,"device_digest":"bad"}'):
                path.write_text(contents, encoding="utf-8")
                self.assertFalse(store.authenticate(credential))
            path.unlink()
            self.assertFalse(store.authenticate(credential))

    def test_failed_persistence_does_not_consume_token(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            store = PairingStore(Path(directory) / "devices.json")
            token = store.issue_token()
            with patch.object(store, "_save_device_digest", side_effect=OSError):
                with self.assertRaises(OSError):
                    store.pair(token)
            credential = store.pair(token)
            self.assertTrue(store.authenticate(credential))


class ProtocolTests(unittest.TestCase):
    def test_text_delta_is_bounded_and_ansi_free(self) -> None:
        event = AgentEvent(
            EventKind.MODEL_EVENT,
            {
                "event": ModelEvent(
                    ModelEventKind.TEXT_DELTA,
                    text="\x1b[31mhello\x1b[0m",
                ).to_dict()
            },
        )

        mobile = event_from_agent(event, task_id="task", session_id="thread", sequence=1)

        self.assertIsNotNone(mobile)
        self.assertEqual(mobile.to_dict()["data"], {"text": "hello"})

    def test_sensitive_or_unrecognized_events_are_not_forwarded(self) -> None:
        event = AgentEvent(EventKind.CONTEXT_BUILT, {"prompt": "secret"})

        self.assertIsNone(event_from_agent(event, task_id="task", session_id="thread", sequence=1))


class _FakeForeground:
    def __init__(self, events: list[AgentEvent]) -> None:
        self.events_to_yield = events
        self.started: list[str] = []
        self.interrupted: list[str] = []

    async def start(self, prompt: str) -> SimpleNamespace:
        self.started.append(prompt)
        return SimpleNamespace(id="task-1", thread_id="thread-1")

    async def events(self, task_id: str):
        for event in self.events_to_yield:
            await asyncio.sleep(0)
            yield event

    async def interrupt(self, task_id: str, reason: str) -> None:
        self.interrupted.append(f"{task_id}:{reason}")


class RemoteTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_events_are_buffered_and_second_start_is_rejected(self) -> None:
        foreground = _FakeForeground(
            [
                AgentEvent(EventKind.TASK_STATUS_CHANGED, {"status": "running"}),
                AgentEvent(
                    EventKind.MODEL_EVENT,
                    {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="answer").to_dict()},
                ),
                AgentEvent(EventKind.COMPLETED),
            ]
        )
        controller = RemoteTaskController(SimpleNamespace(foreground_tasks=foreground))

        ids = await controller.start("prompt")
        with self.assertRaises(RuntimeError):
            await controller.start("second")

        await asyncio.sleep(0.05)
        events = [event async for event in controller.events()]
        self.assertEqual([event.event for event in events], [
            "task_started", "task_status", "assistant_delta", "task_completed"
        ])
        self.assertEqual(ids["task_id"], "task-1")

    async def test_stop_delegates_to_existing_interrupt_path(self) -> None:
        foreground = _FakeForeground([])
        controller = RemoteTaskController(SimpleNamespace(foreground_tasks=foreground))

        await controller.start("prompt")
        await controller.stop("task-1")

        self.assertEqual(foreground.interrupted, ["task-1:remote user stopped task"])


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "devices.json"
        patched = patch("chaos_agent.remote.server.PairingStore", side_effect=lambda: PairingStore(self.path))
        patched.start()
        self.addCleanup(patched.stop)
        state_patch = patch("chaos_agent.remote.server.product_state_root", return_value=Path(directory.name).resolve())
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_http_routes_require_pairing_and_serve_pwa(self) -> None:
        foreground = _FakeForeground([])
        with tempfile.TemporaryDirectory() as directory:
            pairing = PairingStore(Path(directory) / "devices.json")
            app, pairing = create_host_app(SimpleNamespace(foreground_tasks=foreground), pairing=pairing)
            token = pairing.issue_token()

            with TestClient(app) as client:
                self.assertEqual(client.get("/status").status_code, 401)
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn("Chaos Agent", page.text)
                response = client.post("/pair", json={"token": token})
                self.assertEqual(response.status_code, 200)
                credential = response.json()["device_credential"]
                headers = {"authorization": f"Bearer {credential}"}
                self.assertEqual(client.get("/status", headers=headers).status_code, 200)
                self.assertEqual(client.get("/status").status_code, 401)

            restored, _ = create_host_app(
                SimpleNamespace(foreground_tasks=foreground),
                pairing=PairingStore(Path(directory) / "devices.json"),
            )
            with TestClient(restored) as client:
                self.assertEqual(client.get("/status", headers=headers).status_code, 200)

    def test_websocket_rejects_bad_credential_and_accepts_paired_credential(self) -> None:
        foreground = _FakeForeground([AgentEvent(EventKind.COMPLETED)])
        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=foreground))
        token = pairing.issue_token()

        with TestClient(app) as client:
            credential = client.post("/pair", json={"token": token}).json()["device_credential"]
            with self.assertRaises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("/sessions/current/events") as websocket:
                    websocket.send_text("auth:wrong")
                    websocket.receive_text()
            self.assertEqual(getattr(rejected.exception, "code", None), 4401)

            client.post("/sessions/current/messages", headers={"authorization": f"Bearer {credential}"}, json={"prompt": "test"})
            with client.websocket_connect("/sessions/current/events") as websocket:
                websocket.send_text(f"auth:{credential}")
                self.assertEqual(websocket.receive_json()["event"], "task_started")
                self.assertEqual(websocket.receive_json()["event"], "task_completed")

    def test_external_revoke_rejects_http_and_closes_waiting_websocket(self):
        class WaitingForeground(_FakeForeground):
            async def events(self, task_id):
                yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"status": "running"})
                await asyncio.Event().wait()

        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=WaitingForeground([])))
        credential = pairing.pair(pairing.issue_token())
        headers = {"authorization": f"Bearer {credential}"}
        with TestClient(app) as client:
            client.post("/sessions/current/messages", headers=headers, json={"prompt": "test"})
            with client.websocket_connect("/sessions/current/events") as websocket:
                websocket.send_text(f"auth:{credential}")
                self.assertEqual(websocket.receive_json()["event"], "task_started")
                self.assertEqual(websocket.receive_json()["event"], "task_status")
                PairingStore(self.path).revoke()
                self.assertEqual(client.get("/status", headers=headers).status_code, 401)
                with self.assertRaises(WebSocketDisconnect) as rejected:
                    websocket.receive_json()
                self.assertEqual(rejected.exception.code, 4401)

    def test_http_task_stop_and_event_resume(self):
        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=_FakeForeground([AgentEvent(EventKind.COMPLETED)])))
        credential = pairing.pair(pairing.issue_token())
        headers = {"authorization": f"Bearer {credential}"}
        with TestClient(app) as client:
            self.assertEqual(client.get("/status", headers={"authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(client.post("/pair", json={"token": "wrong"}).status_code, 401)
            response = client.post("/sessions/current/messages", headers=headers, json={"prompt": "test"})
            self.assertEqual(response.status_code, 200)
            with client.websocket_connect("/sessions/current/events?since=1") as websocket:
                websocket.send_text(f"auth:{credential}")
                self.assertEqual(websocket.receive_json()["event"], "task_completed")
            self.assertEqual(client.post("/tasks/task-1/stop", headers=headers).status_code, 409)
            self.assertEqual(client.post("/tasks/task-1/stop").status_code, 401)

    def test_websocket_rejects_missing_auth_prefix(self):
        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=_FakeForeground([])))
        credential = pairing.pair(pairing.issue_token())
        with TestClient(app) as client:
            with client.websocket_connect("/sessions/current/events") as websocket:
                websocket.send_text(credential)
                with self.assertRaises(WebSocketDisconnect) as rejected:
                    websocket.receive_text()
                self.assertEqual(rejected.exception.code, 4401)

    def test_bad_pairing_body_and_persistence_failure_can_retry(self):
        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=_FakeForeground([])))
        token = pairing.issue_token()
        with TestClient(app) as client:
            self.assertEqual(client.post("/pair", json=[]).status_code, 400)
            self.assertEqual(client.post("/pair", json={"token": None}).status_code, 401)
            with patch.object(pairing, "_save_device_digest", side_effect=OSError):
                self.assertEqual(client.post("/pair", json={"token": token}).status_code, 503)
            self.assertEqual(client.post("/pair", json={"token": token}).status_code, 200)

    def test_pwa_uses_current_page_origin_for_websocket(self) -> None:
        app, _ = create_host_app(SimpleNamespace(foreground_tasks=_FakeForeground([])))

        with TestClient(app) as client:
            page = client.get("/").text

        self.assertIn("function webSocketUrl()", page)
        self.assertIn("location.protocol==='https:'?'wss':'ws'", page)
        self.assertNotIn("100.", page)


if __name__ == "__main__":
    unittest.main()
