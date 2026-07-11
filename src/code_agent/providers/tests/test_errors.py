from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.errors import (  # noqa: E402
    ProviderConfigError,
    ProviderError,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderResponseLimitError,
)


class ProviderErrorTests(unittest.TestCase):
    def test_error_hierarchy_and_http_metadata(self) -> None:
        error = ProviderHTTPError(status=429, retryable=True)

        self.assertIsInstance(error, ProviderError)
        self.assertEqual(error.status, 429)
        self.assertTrue(error.retryable)
        self.assertIsInstance(ProviderConfigError("bad config"), ProviderError)
        self.assertIsInstance(ProviderProtocolError("bad event"), ProviderError)
        self.assertIsInstance(ProviderResponseLimitError("too large"), ProviderError)

    def test_common_authorization_values_are_redacted(self) -> None:
        secret = "sk-super-secret-value"
        error = ProviderError(
            f"Authorization: Bearer {secret}; api_key={secret}",
            sensitive_values=(secret,),
        )

        rendered = str(error)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_authorization_headers_redact_every_scheme_and_keep_context(self) -> None:
        cases = (
            (
                "request failed; Authorization: Basic c2VjcmV0; attempt=2",
                "c2VjcmV0",
                ("request failed", "attempt=2"),
            ),
            (
                "before\npRoXy-AuThOrIzAtIoN: Custom token with parts\nafter",
                "token with parts",
                ("before", "after"),
            ),
        )

        for message, secret, context in cases:
            with self.subTest(message=message):
                rendered = str(ProviderError(message))
                self.assertNotIn(secret, rendered)
                self.assertIn("[REDACTED]", rendered)
                for text in context:
                    self.assertIn(text, rendered)

    def test_structured_messages_are_deeply_redacted_without_mutation(self) -> None:
        message = {
            "status": "failed",
            "Authorization": "Basic auth-value",
            "nested": [
                {
                    "Proxy-Authorization": "Digest proxy-value",
                    "x-api-key": "x-key-value",
                    "apiKey": "camel-key-value",
                    "token": "token-value",
                    "secret": {"raw": "nested-secret-value"},
                    "safe": "visible context",
                },
                {"password": "password-value", "Cookie": "session-value"},
            ],
            "tuple": ({"clientSecret": "client-secret-value"},),
        }
        original = copy.deepcopy(message)

        rendered = str(ProviderError(message))

        for secret in (
            "auth-value",
            "proxy-value",
            "x-key-value",
            "camel-key-value",
            "token-value",
            "nested-secret-value",
            "password-value",
            "session-value",
            "client-secret-value",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("visible context", rendered)
        self.assertIn("failed", rendered)
        self.assertGreaterEqual(rendered.count("[REDACTED]"), 9)
        self.assertEqual(message, original)

    def test_structured_redaction_handles_dict_and_list_cycles(self) -> None:
        message: dict[str, object] = {
            "safe": "cycle context",
            "token": "cycle-token-value",
        }
        values: list[object] = [
            {"Proxy-Authorization": "Basic cycle-proxy-value"}
        ]
        message["self"] = message
        message["values"] = values
        values.append(values)

        rendered = str(ProviderError(message))

        self.assertNotIn("cycle-token-value", rendered)
        self.assertNotIn("cycle-proxy-value", rendered)
        self.assertIn("cycle context", rendered)
        self.assertIn("[CIRCULAR]", rendered)
        self.assertIs(message["self"], message)
        self.assertIs(values[1], values)


if __name__ == "__main__":
    unittest.main()
