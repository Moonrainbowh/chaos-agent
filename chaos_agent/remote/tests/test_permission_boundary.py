"""Business permissions must not invalidate a correctly paired device."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from code_agent.project_launcher.store import ProjectStore
from chaos_agent.remote.pairing import PairingStore
from chaos_agent.remote.server import create_host_app


class PermissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="remote-permission-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        devices, projects = root / "devices.json", root / "projects.json"
        for path in (devices, projects):
            self.assertTrue(path.is_absolute() and path.resolve().is_relative_to(root))
        self.pairing = PairingStore(devices)
        self.credential = self.pairing.pair(self.pairing.issue_token())
        app, _ = create_host_app(SimpleNamespace(foreground_tasks=SimpleNamespace()),
            pairing=self.pairing, project_store=ProjectStore(projects))
        self.client = self.enterContext(TestClient(app))
        self.headers = {"authorization": "Bearer " + self.credential}

    def test_business_permission_denied_preserves_device_and_hides_details(self):
        denied = AsyncMock(side_effect=PermissionError("private filesystem detail"))
        with patch("chaos_agent.remote.server.RemoteTaskController.start", denied):
            response = self.client.post("/sessions/current/messages", headers=self.headers,
                json={"prompt": "fixed offline fixture"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private filesystem detail", response.text)
        self.assertTrue(self.pairing.authenticate(self.credential))
        denied.assert_awaited_once()

    def test_revoked_device_fails_before_business_handler(self):
        self.pairing.revoke()
        handler = AsyncMock()
        with patch("chaos_agent.remote.server.RemoteTaskController.start", handler):
            response = self.client.post("/sessions/current/messages", headers=self.headers,
                json={"prompt": "fixed offline fixture"})
        self.assertEqual(response.status_code, 401)
        handler.assert_not_awaited()

    def test_response_gate_rechecks_after_http_authentication(self):
        handler = AsyncMock()
        with patch.object(self.pairing, "authenticate", side_effect=(True, False)), \
                patch("chaos_agent.remote.server.RemoteRequestControl.respond", handler):
            response = self.client.post("/tasks/task-1/requests/request-1/respond",
                headers=self.headers, json={"approved": True})
        self.assertEqual(response.status_code, 401)
        handler.assert_not_awaited()

    def test_callback_recheck_failure_remains_unauthorized(self):
        async def respond(_task_id, _body, authenticate):
            await authenticate()
            self.fail("revoked callback must not reach consumption")
        with patch.object(self.pairing, "authenticate", side_effect=(True, True, False)), \
                patch("chaos_agent.remote.server.RemoteRequestControl.respond", side_effect=respond):
            response = self.client.post("/tasks/task-1/requests/request-1/respond",
                headers=self.headers, json={"approved": True})
        self.assertEqual(response.status_code, 401)

    def test_business_denial_inside_response_gate_is_forbidden(self):
        handler = AsyncMock(side_effect=PermissionError("private operation detail"))
        with patch("chaos_agent.remote.server.RemoteRequestControl.respond", handler):
            response = self.client.post("/tasks/task-1/requests/request-1/respond",
                headers=self.headers, json={"approved": True})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private operation detail", response.text)
        self.assertTrue(self.pairing.authenticate(self.credential))
        handler.assert_awaited_once()
