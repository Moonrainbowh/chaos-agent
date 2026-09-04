from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.interfaces.diagnostic_view import format_diagnostics
from code_agent_win.system_diagnostics import SystemDoctor


class _PowerShell:
    def resolve(self) -> object:
        return SimpleNamespace(version="7.5.2", summary="powershell_7 test runtime")


class SystemDoctorTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_real_probe_success_without_http_or_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            doctor = SystemDoctor(
                Path(directory),
                powershell=_PowerShell(),
                git=object(),
                base_url="https://api.example.test/v1",
            )
            with patch("code_agent_win.system_diagnostics._tcp_probe") as probe:
                checks = await doctor.run()

        endpoint = next(item for item in checks if item.name == "Provider endpoint")
        self.assertEqual(endpoint.status, "pass")
        probe.assert_called_once_with("api.example.test", 443, 2.0)
        self.assertNotIn("api_key", format_diagnostics(checks))

    async def test_failed_tcp_probe_is_not_reported_as_connected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            doctor = SystemDoctor(
                Path(directory),
                powershell=_PowerShell(),
                git=None,
                base_url="http://offline.example.test:8080",
            )
            with patch(
                "code_agent_win.system_diagnostics._tcp_probe",
                side_effect=OSError("offline"),
            ):
                checks = await doctor.run()

        endpoint = next(item for item in checks if item.name == "Provider endpoint")
        self.assertEqual(endpoint.status, "fail")
        self.assertIn("TCP connection failed", endpoint.detail)


if __name__ == "__main__":
    unittest.main()
