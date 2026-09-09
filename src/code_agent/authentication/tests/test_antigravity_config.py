import os
import unittest
from unittest.mock import AsyncMock, patch

from code_agent.authentication.flows_antigravity import identity, login_antigravity, refresh_antigravity
from code_agent.authentication.models import AuthError, Credential


class AntigravityConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_identity_requires_both_values_without_echoing_configured_value(self):
        names = ("ANTIGRAVITY_OAUTH_CLIENT_ID", "ANTIGRAVITY_OAUTH_CLIENT_SECRET")
        for absent in names:
            with self.subTest(absent=absent), patch.dict(os.environ, {}, clear=True):
                os.environ[names[1 - names.index(absent)]] = "private-test-value"
                with self.assertRaises(AuthError) as error:
                    identity()
                self.assertIn(absent, str(error.exception))
                self.assertNotIn("private-test-value", str(error.exception))

    def test_identity_reads_current_environment_and_rejects_blank_values(self):
        with patch.dict(os.environ, {
            "ANTIGRAVITY_OAUTH_CLIENT_ID": " test-client ",
            "ANTIGRAVITY_OAUTH_CLIENT_SECRET": " test-secret ",
        }, clear=True):
            self.assertEqual(identity(), ("test-client", "test-secret"))
            os.environ["ANTIGRAVITY_OAUTH_CLIENT_SECRET"] = " "
            with self.assertRaises(AuthError):
                identity()

    async def test_missing_configuration_stops_login_and_refresh_before_io(self):
        client = AsyncMock()
        with patch.dict(os.environ, {}, clear=True), patch(
            "code_agent.authentication.flows_antigravity.BrowserCallback"
        ) as callback:
            with self.assertRaises(AuthError):
                await login_antigravity(client, AsyncMock(), AsyncMock())
            with self.assertRaises(AuthError):
                await refresh_antigravity(client, Credential("oauth", "test-access", "test-refresh"))
            callback.assert_not_called()
            self.assertEqual(client.mock_calls, [])
