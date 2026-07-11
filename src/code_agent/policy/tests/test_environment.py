from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.policy.environment import redact_sensitive, sanitize_environment  # noqa: E402


class SanitizeEnvironmentTests(unittest.TestCase):
    def test_only_windows_runtime_defaults_are_inherited(self) -> None:
        host = {
            "PATH": "bin",
            "SystemRoot": "C:\\Windows",
            "COMSPEC": "cmd.exe",
            "PATHEXT": ".EXE;.CMD",
            "TEMP": "temp",
            "TMP": "tmp",
            "USERPROFILE": "C:\\Users\\tester",
            "HOME": "should-not-pass",
            "PYTHONPATH": "should-not-pass",
        }

        sanitized = sanitize_environment(host)

        self.assertEqual(
            sanitized,
            {
                "PATH": "bin",
                "SystemRoot": "C:\\Windows",
                "COMSPEC": "cmd.exe",
                "PATHEXT": ".EXE;.CMD",
                "TEMP": "temp",
                "TMP": "tmp",
                "USERPROFILE": "C:\\Users\\tester",
            },
        )

    def test_windows_names_are_matched_case_insensitively(self) -> None:
        host = {
            "path": "bin",
            "SYSTEMroot": "C:\\Windows",
            "comspec": "cmd.exe",
            "temp": "temp",
        }

        sanitized = sanitize_environment(host)

        self.assertEqual(
            sanitized,
            {
                "PATH": "bin",
                "SystemRoot": "C:\\Windows",
                "COMSPEC": "cmd.exe",
                "TEMP": "temp",
            },
        )

    def test_sensitive_host_values_are_dropped_by_default(self) -> None:
        host = {
            "PATH": "bin",
            "OPENAI_API_KEY": "openai-secret",
            "anthropic_api_key": "anthropic-secret",
            "ACCESS_TOKEN": "token-secret",
            "CLIENT_SECRET": "client-secret",
            "DB_PASSWORD": "password-secret",
            "SESSION_COOKIE": "cookie-secret",
        }

        self.assertEqual(sanitize_environment(host), {"PATH": "bin"})

    def test_explicitly_allowed_names_are_inherited_case_insensitively(self) -> None:
        host = {"custom_flag": "on", "OPENAI_API_KEY": "approved-secret"}

        sanitized = sanitize_environment(
            host, allowed_names=("CUSTOM_FLAG", "OpenAI_API_Key")
        )

        self.assertEqual(
            sanitized,
            {"CUSTOM_FLAG": "on", "OpenAI_API_Key": "approved-secret"},
        )

    def test_explicit_values_only_override_approved_names(self) -> None:
        host = {"PATH": "host-bin", "custom_flag": "host-value"}
        explicit = {
            "path": "explicit-bin",
            "CUSTOM_FLAG": "explicit-value",
            "UNAPPROVED": "must-not-pass",
            "ACCESS_TOKEN": "must-not-pass",
        }

        sanitized = sanitize_environment(
            host,
            allowed_names=("Custom_Flag",),
            explicit_env=explicit,
        )

        self.assertEqual(
            sanitized,
            {"PATH": "explicit-bin", "Custom_Flag": "explicit-value"},
        )


class RedactSensitiveTests(unittest.TestCase):
    def test_redaction_handles_cyclic_nested_values(self) -> None:
        value: dict[str, object] = {"api_key": "test-local-key-7xK2"}
        value["self"] = value

        redacted = redact_sensitive(value)

        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertIs(redacted["self"], redacted)
        self.assertNotIn("test-local-key-7xK2", repr(redacted))

    def test_redacts_camel_case_compound_secret_keys_without_mutating_input(self) -> None:
        value = {
            "apiKey": "api-key",
            "nested": {
                "accessToken": "access-token",
                "clientSecret": "client-secret",
                "privateKey": "private-key",
            },
        }
        original = copy.deepcopy(value)

        redacted = redact_sensitive(value)

        self.assertEqual(value, original)
        self.assertEqual(redacted["apiKey"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["accessToken"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["clientSecret"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["privateKey"], "[REDACTED]")

    def test_redacts_sensitive_keys_recursively(self) -> None:
        value = {
            "authorization": "Bearer abc",
            "nested": {
                "OpenAI_API_Key": "key",
                "items": [
                    {"access_token": "token", "safe": "visible"},
                    {"session_cookie": "cookie"},
                ],
            },
            "password_hint": "also-sensitive",
            "ordinary": "visible",
        }

        redacted = redact_sensitive(value)

        self.assertEqual(redacted["authorization"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["OpenAI_API_Key"], "[REDACTED]")
        self.assertEqual(
            redacted["nested"]["items"][0]["access_token"], "[REDACTED]"
        )
        self.assertEqual(redacted["nested"]["items"][0]["safe"], "visible")
        self.assertEqual(
            redacted["nested"]["items"][1]["session_cookie"], "[REDACTED]"
        )
        self.assertEqual(redacted["password_hint"], "[REDACTED]")
        self.assertEqual(redacted["ordinary"], "visible")

    def test_redaction_does_not_modify_input_and_preserves_sequence_shapes(self) -> None:
        value = {
            "items": (
                {"client_secret": "secret"},
                ["visible", {"password": "password"}],
            )
        }
        original = copy.deepcopy(value)

        redacted = redact_sensitive(value)

        self.assertEqual(value, original)
        self.assertIsInstance(redacted["items"], tuple)
        self.assertIsInstance(redacted["items"][1], list)
        self.assertEqual(redacted["items"][0]["client_secret"], "[REDACTED]")
        self.assertEqual(redacted["items"][1][1]["password"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
