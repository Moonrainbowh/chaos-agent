from __future__ import annotations

import asyncio
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from code_agent.project_launcher.store import ProjectStore
from chaos_agent.remote.server import create_host_app
from chaos_agent.remote.tests.navigation_support import NavigationFixture


class ProjectRegistryTests(NavigationFixture, unittest.TestCase):
    def add(self, client, root):
        response = client.post("/projects", headers=self.headers, json={"path": str(root)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def test_shared_ssh_registry_and_web_restart_preserve_recent_without_history(self):
        self.project_store.add(self.other)
        self.project_store.select(self.other)
        with TestClient(self.app) as client:
            row = self.project(client, self.other)
            self.assertTrue(row["registered"])
            self.assertTrue(row["recent"])
            self.assertEqual(row["session_count"], 0)
            first = self.project(client, self.root)
            response = client.post(f"/projects/{first['id']}/select", headers=self.headers)
            self.assertTrue(response.json()["project"]["recent"])
            self.assertEqual(self.factory_roots, [])
        store = ProjectStore(self.project_store.path)
        restored, _ = create_host_app(self.application, pairing=self.pairing, project_store=store)
        with TestClient(restored) as client:
            self.assertTrue(self.project(client, self.root)["recent"])
            self.assertTrue(self.project(client, self.other)["registered"])
        self.assertEqual(store.last_root(), self.root)

    def test_first_session_in_registered_project_uses_selected_root_without_old_history(self):
        with TestClient(self.app) as client:
            row = self.add(client, self.other)
            response = client.post("/sessions", headers=self.headers, json={"project_id": row["id"]})
            self.assertEqual(response.status_code, 200)
            identifier = response.json()["session_id"]
            self.assertEqual(self.bindings, [(identifier, self.other)])
            self.assertEqual(self.factory_roots, [])
            result = client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "first"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.factory_roots, [self.other])
        checkpoints = asyncio.run(self.sessions.list_checkpoints(identifier))
        self.assertEqual(checkpoints[0].metadata["source_root"], str(self.other))

    def test_remove_keeps_directory_and_history_and_does_not_reseed_empty_entry(self):
        marker = self.other / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with TestClient(self.app) as client:
            row = self.add(client, self.other)
            self.assertEqual(client.delete(f"/projects/{row['id']}", headers=self.headers).status_code, 200)
            self.assertFalse(any(item["id"] == row["id"] for item in client.get("/projects", headers=self.headers).json()["projects"]))
            self.assertEqual(marker.read_text(), "keep")
            self.project_store.seed((self.other,))
            self.assertFalse(any(entry.root == self.other for entry in self.project_store.entries()))
            self.add(client, self.other)
            identifier = self.seed(self.other, messages=(("user", "saved"),))
            self.assertEqual(client.delete(f"/projects/{row['id']}", headers=self.headers).status_code, 200)
            history_project = self.project(client, self.other)
            self.assertFalse(history_project["registered"])
            history = client.get(f"/sessions/{identifier}/messages", headers=self.headers)
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()["messages"][0]["content"], "saved")
            self.assertTrue(marker.exists())

    def test_directory_navigation_and_absolute_existing_validation(self):
        (self.other / "child").mkdir()
        (self.other / ".git").mkdir()
        with TestClient(self.app) as client:
            roots = client.get("/project-directories", headers=self.headers).json()
            self.assertIsNone(roots["path"])
            self.assertTrue(roots["directories"])
            result = client.get("/project-directories", headers=self.headers, params={"path": str(self.other)})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), {"path": str(self.other), "parent": str(self.other.parent),
                                            "directories": [{"name": "child", "path": str(self.other / "child")}]})
            for value in ("relative", str(self.directory / "missing"), ""):
                self.assertEqual(client.get("/project-directories", headers=self.headers, params={"path": value}).status_code, 400)
                self.assertEqual(client.post("/projects", headers=self.headers, json={"path": value}).status_code, 400)
            self.assertEqual(client.post("/projects", headers=self.headers, json={"path": 123}).status_code, 400)
            for method, route in (("POST", "/projects/missing/select"), ("DELETE", "/projects/missing")):
                self.assertEqual(client.request(method, route, headers=self.headers).status_code, 404)

    def test_registry_routes_authenticate_before_parsing(self):
        with TestClient(self.app) as client:
            for method, route in (("POST", "/projects"), ("DELETE", "/projects/!"),
                                  ("POST", "/projects/!/select"), ("GET", "/project-directories?path=relative")):
                self.assertEqual(client.request(method, route, json=[]).status_code, 401)
        self.assertFalse(self.project_store.path.exists())

    def test_selection_and_removal_preserve_running_task_identity(self):
        self.foreground.hold = True
        with TestClient(self.app) as client:
            other = self.add(client, self.other)
            identifier = self.empty(client, self.root)
            run = client.post(f"/sessions/{identifier}/messages", headers=self.headers, json={"prompt": "hold"}).json()
            selected = client.post(f"/projects/{other['id']}/select", headers=self.headers)
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(client.delete(f"/projects/{other['id']}", headers=self.headers).status_code, 200)
            status = client.get("/status", headers=self.headers).json()
            self.assertEqual(status["task_id"], run["task_id"])
            self.assertEqual(status["session_id"], identifier)
            self.assertEqual(self.factory_roots, [])
            self.assertEqual(self.application.workspace_root, self.root)
            self.assertEqual(client.post(f"/tasks/{run['task_id']}/stop", headers=self.headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
