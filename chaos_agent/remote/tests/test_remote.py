from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from starlette.testclient import TestClient

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind

from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote.protocol import event_from_agent
from chaos_agent.remote.server import create_host_app
from chaos_agent.remote.task_controller import RemoteTaskController


class PairingTests(unittest.TestCase):
    def test_token_is_single_use_and_credential_is_distinct(self) -> None:
        store = PairingStore()
        token = store.issue_token()
        credential = store.pair(token)

        self.assertTrue(store.authenticate(credential))
        self.assertFalse(store.authenticate(token))
        with self.assertRaises(PermissionError):
            store.pair(token)

    def test_reissue_revokes_previous_device(self) -> None:
        store = PairingStore()
        first = store.pair(store.issue_token())
        second = store.pair(store.issue_token())

        self.assertFalse(store.authenticate(first))
        self.assertTrue(store.authenticate(second))


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
    def test_http_routes_require_pairing_and_serve_pwa(self) -> None:
        foreground = _FakeForeground([])
        app, pairing = create_host_app(SimpleNamespace(foreground_tasks=foreground))
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


if __name__ == "__main__":
    unittest.main()
