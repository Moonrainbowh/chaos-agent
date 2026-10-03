from __future__ import annotations

import asyncio
import sqlite3
import unittest

from starlette.testclient import TestClient

from chaos_agent.remote.server import create_host_app
from chaos_agent.remote.tests.navigation_support import NavigationFixture


class NavigationTests(NavigationFixture, unittest.TestCase):
    def test_every_catalog_route_authenticates_before_parsing(self):
        with TestClient(self.app) as client:
            for method, path in (("GET", "/projects"), ("GET", "/sessions?offset=bad"), ("POST", "/sessions"), ("GET", "/sessions/missing/messages?limit=bad"), ("POST", "/sessions/missing/messages")):
                response = client.request(method, path, json=[])
                self.assertEqual(response.status_code, 401, (method, path))
            for path in ("/sessions?offset=-1", "/sessions?limit=0", "/sessions?limit=101", "/sessions?offset=1.5", "/sessions/missing/messages?before=bad"):
                self.assertEqual(client.get(path, headers=self.headers).status_code, 400, path)
            self.assertEqual(client.get("/sessions?project_id=missing", headers=self.headers).status_code, 404)
            self.assertEqual(client.get("/sessions/missing/messages", headers=self.headers).status_code, 404)
            self.assertEqual(client.post("/sessions", headers=self.headers, json=[]).status_code, 400)
            self.assertEqual(client.post("/sessions/current/messages", headers=self.headers, json={"prompt": "x" * 1025}).status_code, 400)

    def test_projects_search_public_history_and_restart_share_real_database(self):
        first = self.seed(self.root, title="first", messages=(("user", "buried user needle"), ("assistant", "last answer")))
        second = self.seed(self.other, title="second", messages=(("assistant", "buried assistant marker"), ("user", "last request")))
        unassigned = self.seed(messages=(("user", "legacy history"), ("tool", "private tool diagnostic")))
        with TestClient(self.app) as client:
            projects = client.get("/projects", headers=self.headers).json()["projects"]
            self.assertEqual(len(projects), 3)
            self.assertEqual(self.project(client, self.other)["session_count"], 1)
            all_rows = client.get("/sessions", headers=self.headers).json()["sessions"]
            legacy = next(row for row in all_rows if row["id"] == unassigned)
            self.assertEqual(legacy["project_id"], "unassigned")
            self.assertEqual(legacy["preview"], "legacy history")
            for query, identifier in (("needle", first), ("marker", second), ("first", first), (second, second)):
                rows = client.get("/sessions", headers=self.headers, params={"q": query}).json()["sessions"]
                self.assertEqual([row["id"] for row in rows], [identifier])
            self.assertEqual(client.get("/sessions", headers=self.headers, params={"q": "private tool"}).json()["sessions"], [])
            self.assertEqual(self.factory_roots, [])
        restored, _ = create_host_app(self.application, pairing=self.pairing, application_factory=self.factory)
        with TestClient(restored) as client:
            history = client.get(f"/sessions/{first}/messages", headers=self.headers).json()
            self.assertEqual([item["content"] for item in history["messages"]], ["buried user needle", "last answer"])
            self.assertEqual(history["event_sequence"], 0)
            self.assertIsNone(history["active_task"])

    def test_session_pages_continue_beyond_one_thousand(self):
        # Populate a large, valid real database in one transaction for this test.
        with sqlite3.connect(self.database) as connection:
            connection.executemany(
                "INSERT INTO threads(id,created_at,updated_at,title,status) VALUES (?,?,?,?,?)",
                [(f"thread-{number:04d}", "2026-10-02T00:00:00+00:00", "2026-10-02T00:00:00+00:00", f"title {number}", "active") for number in range(1003)],
            )
        connection.close()
        with TestClient(self.app) as client:
            page = client.get("/sessions?offset=990&limit=10", headers=self.headers).json()
            self.assertEqual(len(page["sessions"]), 10)
            self.assertEqual(page["next_offset"], 1000)
            tail = client.get("/sessions?offset=1000&limit=10", headers=self.headers).json()
            self.assertEqual(len(tail["sessions"]), 3)
            self.assertIsNone(tail["next_offset"])
            self.assertEqual(tail["sessions"][0]["id"], "thread-1000")

    def test_message_cursor_pages_skip_nonpublic_roles_without_losing_rows(self):
        identifier = self.seed(self.root, messages=(("user", "one"), ("tool", "hidden"), ("assistant", "two"), ("user", "three"), ("tool", "hidden again"), ("assistant", "four")))
        with TestClient(self.app) as client:
            page = client.get(f"/sessions/{identifier}/messages?limit=2", headers=self.headers).json()
            self.assertEqual([item["content"] for item in page["messages"]], ["three", "four"])
            self.assertIsNotNone(page["next_before"])
            older = client.get(f"/sessions/{identifier}/messages", headers=self.headers, params={"limit": 2, "before": page["next_before"]}).json()
            self.assertEqual([item["content"] for item in older["messages"]], ["one", "two"])
            self.assertIsNone(older["next_before"])

    def test_empty_cross_project_session_checkpoint_is_bound_and_survives_restart(self):
        self.seed(self.other)
        with TestClient(self.app) as client:
            identifier = self.empty(client, self.other)
            self.assertIn((identifier, self.other), self.bindings)
            self.assertEqual(self.factory_roots, [])
        restored, _ = create_host_app(self.application, pairing=self.pairing)
        with TestClient(restored) as client:
            item = client.get(f"/sessions/{identifier}/messages", headers=self.headers).json()["session"]
            self.assertEqual(item["project_name"], "project-b")
            self.assertEqual(item["message_count"], 0)
        checkpoints = asyncio.run(self.sessions.list_checkpoints(identifier))
        self.assertEqual(checkpoints[0].metadata["source_root"], str(self.other))

    def test_orphaned_followup_keeps_parent_project_and_unavailable_projects_stay_readable(self):
        parent = self.seed(self.other, messages=(("user", "source context"),))
        child = asyncio.run(self.sessions.create_thread_from_history(parent))
        missing = self.directory / "missing-project"
        unavailable = self.seed(missing, messages=(("assistant", "saved answer"),))
        with TestClient(self.app) as client:
            inherited = client.get(f"/sessions/{child}/messages", headers=self.headers).json()
            self.assertEqual(inherited["session"]["project_id"], self.project(client, self.other)["id"])
            self.assertEqual(self.project(client, missing)["available"], False)
            self.assertEqual(client.get(f"/sessions/{unavailable}/messages", headers=self.headers).status_code, 200)
            self.assertEqual(client.post(f"/sessions/{unavailable}/messages", headers=self.headers, json={"prompt": "run"}).status_code, 409)
            self.assertEqual(self.factory_roots, [])


if __name__ == "__main__":
    unittest.main()
