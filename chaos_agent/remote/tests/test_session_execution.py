from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskStatus
from code_agent.interfaces.task_controller import _owner_is_alive
from code_agent.sessions.errors import SessionStorageError
from chaos_agent.remote.tests.navigation_support import NavigationFixture


class SessionExecutionTests(NavigationFixture, unittest.TestCase):
    def test_selected_empty_session_and_cross_project_terminal_followup_use_real_ids(self):
        previous = self.seed(self.other, messages=(("user", "original context"), ("assistant", "original reply")))
        with TestClient(self.app) as client:
            empty = self.empty(client, self.root)
            response = client.post(f"/sessions/{empty}/messages", headers=self.headers, json={"prompt": "new request"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["session_id"], empty)
            self.assertEqual(self.foreground.started[0][1], empty)
            with client.websocket_connect(f"/sessions/{empty}/events") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                while websocket.receive_json()["event"] != "task_completed":
                    pass
            response = client.post(f"/sessions/{previous}/messages", headers=self.headers, json={"prompt": "follow up"})
            self.assertEqual(response.status_code, 200, response.text)
            continued = response.json()
            self.assertNotEqual(continued["session_id"], previous)
            self.assertEqual(continued["continued_from"], previous)
            self.assertEqual(self.factory_roots, [self.other])
            with client.websocket_connect(f"/sessions/{continued['session_id']}/events") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                while websocket.receive_json()["event"] != "task_completed":
                    pass
            content = client.get(f"/sessions/{continued['session_id']}/messages", headers=self.headers).json()["messages"]
            self.assertEqual([row["content"] for row in content], ["original context", "original reply", "follow up", "live answer"])
            old = client.get(f"/sessions/{previous}/messages", headers=self.headers).json()
            self.assertEqual(old["task"]["status"], "completed")
            # A stream for the original session never follows the new task.
            with client.websocket_connect(f"/sessions/{previous}/events") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                with self.assertRaises(WebSocketDisconnect) as closed:
                    websocket.receive_json()
                self.assertEqual(closed.exception.code, 1000)
        self.assertTrue(self.children[0].closed)

    def test_paused_session_resumes_existing_task_and_busy_project_is_rejected_before_restore(self):
        paused = self.seed(self.root, status=TaskStatus.PAUSED)
        active = self.seed(self.root, status=TaskStatus.RUNNING)
        asyncio.run(self.sessions.archive_thread(active))
        with TestClient(self.app) as client:
            blocked = client.post(f"/sessions/{paused}/messages", headers=self.headers, json={"prompt": "resume"})
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(self.foreground.restored, [])
            async def finish_active():
                task = await self.sessions.load_task_for_thread(active)
                await self.sessions.transition_task(task.id, TaskStatus.COMPLETED)
            client.portal.call(finish_active)
            response = client.post(f"/sessions/{paused}/messages", headers=self.headers, json={"prompt": "resume"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["session_id"], paused)
            self.assertNotIn("continued_from", response.json())
            self.assertEqual(self.foreground.started, [])
            self.assertEqual(len(self.foreground.restored), 1)

    def test_one_host_slot_rejects_other_project_without_materializing_application(self):
        self.seed(self.other)
        self.foreground.hold = True
        with TestClient(self.app) as client:
            first, second = self.empty(client, self.root), self.empty(client, self.other)
            first_run = client.post(f"/sessions/{first}/messages", headers=self.headers, json={"prompt": "long task"})
            self.assertEqual(first_run.status_code, 200)
            second_run = client.post(f"/sessions/{second}/messages", headers=self.headers, json={"prompt": "other task"})
            self.assertEqual(second_run.status_code, 409)
            self.assertEqual(self.factory_roots, [])

    def test_active_history_snapshot_and_cursor_prevent_delta_replay_duplicates(self):
        identifier = self.seed(self.root, status=TaskStatus.PAUSED, messages=(("user", "old user"), ("assistant", "old reply")))
        self.foreground.hold = True
        with TestClient(self.app) as client:
            client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "live prompt"})
            with client.websocket_connect(f"/sessions/{identifier}/events") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                while websocket.receive_json()["event"] != "assistant_delta":
                    pass
            snapshot = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()
            self.assertEqual([row["content"] for row in snapshot["messages"]], ["old user", "old reply", "live prompt", "live answer"])
            self.assertEqual([row["sequence"] is None for row in snapshot["messages"]], [False, False, True, True])
            self.assertTrue(snapshot["assistant_open"])
            self.assertEqual(snapshot["active_prompt"], "live prompt")
            with client.websocket_connect(f"/sessions/{identifier}/events?since={snapshot['event_sequence']}") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                client.portal.call(self.foreground.release.set)
                self.assertEqual(websocket.receive_json()["event"], "task_completed")
            completed = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()
            self.assertEqual([row["content"] for row in completed["messages"]], ["old user", "old reply", "live prompt", "live answer"])
            self.assertTrue(all(isinstance(row["sequence"], int) for row in completed["messages"]))
            self.assertIsNone(completed["active_task"])

    def test_completion_during_base_read_returns_final_durable_history(self):
        identifier = self.seed(self.root, status=TaskStatus.PAUSED)
        self.foreground.hold = True
        with TestClient(self.app) as client:
            client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "race prompt"})
            with client.websocket_connect(f"/sessions/{identifier}/events") as websocket:
                websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                while websocket.receive_json()["event"] != "assistant_delta":
                    pass
            tasks = self.app.state.remote_tasks
            original = tasks.catalog.message_page
            once = False
            async def raced(*args, **kwargs):
                nonlocal once
                result = await original(*args, **kwargs)
                if not once:
                    once = True
                    self.foreground.release.set()
                    await tasks._active.runner
                return result
            with patch.object(tasks.catalog, "message_page", side_effect=raced):
                result = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()
            self.assertEqual([row["content"] for row in result["messages"]], ["race prompt", "live answer"])
            self.assertIsNone(result["active_task"])
            self.assertEqual(result["task"]["status"], "completed")

    def test_stale_owner_reconciliation_on_host_start_allows_resume(self):
        identifier = self.seed(self.root, status=TaskStatus.RUNNING)
        async def register():
            task = await self.sessions.load_task_for_thread(identifier)
            await self.sessions.register_task_execution(task.id, "expired-owner", 999999999, 1.0)
        asyncio.run(register())
        async def reconcile():
            return await self.sessions.reconcile_stale_tasks(_owner_is_alive)
        self.foreground.reconcile_stale_tasks = reconcile
        with TestClient(self.app) as client:
            response = client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "after restart"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["session_id"], identifier)

    def test_backend_failure_never_exposes_provider_exception(self):
        empty = None
        with TestClient(self.app) as client:
            empty = self.empty(client, self.root)
            with patch.object(self.foreground, "start", side_effect=RuntimeError("api_key=private-test-value")):
                result = client.post(f"/sessions/{empty}/messages", headers=self.headers, json={"prompt": "new request"})
            self.assertEqual(result.status_code, 409)
            self.assertNotIn("private-test-value", result.text)

    def test_event_setup_failure_leaves_new_task_interrupted_and_resumable(self):
        with TestClient(self.app) as client:
            identifier = self.empty(client, self.root)
            original = self.sessions.load_message_records
            async def failed_base(thread_id, **kwargs):
                if kwargs.get("limit") == 1:
                    raise SessionStorageError("injected base read failure")
                return await original(thread_id, **kwargs)
            with patch.object(self.sessions, "load_message_records", side_effect=failed_base):
                result = client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "request"})
            self.assertEqual(result.status_code, 503)
            history = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()
            self.assertEqual(history["task"]["status"], "interrupted")
            self.assertEqual(client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "resume"}).status_code, 200)

    def test_event_stream_setup_failure_interrupts_durable_task_without_exposing_exception(self):
        async def failing_events(identifier, prompt=None):
            await self.sessions.transition_task(identifier, TaskStatus.RUNNING)
            yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"status": "running"})
            raise RuntimeError("api_key=private-event-value")

        with TestClient(self.app) as client:
            identifier = self.empty(client, self.root)
            with patch.object(self.foreground, "events", side_effect=failing_events):
                client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "request"})
                with client.websocket_connect(f"/sessions/{identifier}/events") as websocket:
                    websocket.send_text(self.headers["authorization"].replace("Bearer ", "auth:"))
                    while True:
                        event = websocket.receive_json()
                        self.assertNotIn("private-event-value", str(event))
                        if event["event"] == "task_failed":
                            break
            history = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()
            self.assertEqual(history["task"]["status"], "interrupted")
            self.assertEqual(client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "resume"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
