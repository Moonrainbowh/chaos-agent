from __future__ import annotations

import os
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.config import (  # noqa: E402
    ApiProtocol,
    ConfiguredApiKey,
    ModelProfile,
    ModelProfileResolver,
    ProviderConfig,
)
from code_agent.providers.errors import ProviderConfigError  # noqa: E402


class ProviderConfigTests(unittest.TestCase):
    def make_config(self, **overrides: object) -> ProviderConfig:
        values = {
            "base_url": "https://api.example.test",
            "model": "model-1",
            "api": ApiProtocol.RESPONSES,
            "api_key_env": "EXAMPLE_API_KEY",
            "timeout_s": 30.0,
            "max_retries": 2,
            "max_event_bytes": 4096,
        }
        values.update(overrides)
        return ProviderConfig(**values)  # type: ignore[arg-type]

    def test_config_is_frozen_and_has_safe_default_paths(self) -> None:
        config = self.make_config()

        self.assertEqual(config.responses_path, "/v1/responses")
        self.assertEqual(config.chat_completions_path, "/v1/chat/completions")
        self.assertEqual(config.anthropic_messages_path, "/v1/messages")
        self.assertEqual(config.max_response_bytes, 8 * 1024 * 1024)
        self.assertEqual(config.max_tool_argument_bytes, 1024 * 1024)
        self.assertEqual(config.max_tool_calls, 64)
        with self.assertRaises(FrozenInstanceError):
            config.model = "changed"  # type: ignore[misc]

    def test_config_rejects_invalid_urls_and_paths(self) -> None:
        invalid = (
            {"base_url": "ftp://api.example.test"},
            {"base_url": "https://user:secret@api.example.test"},
            {"base_url": "https://api.example.test?token=secret"},
            {"responses_path": "v1/responses"},
            {"responses_path": "//evil.example/v1/responses"},
            {"responses_path": "/v1/../admin"},
            {"responses_path": "/v1/responses?key=x"},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ProviderConfigError):
                    self.make_config(**values)

    def test_config_rejects_blank_or_non_finite_limits(self) -> None:
        invalid = (
            {"model": " "},
            {"api_key_env": ""},
            {"timeout_s": 0},
            {"timeout_s": float("inf")},
            {"max_retries": -1},
            {"max_retries": 1.5},
            {"max_event_bytes": 0},
            {"max_response_bytes": 0},
            {"max_tool_argument_bytes": -1},
            {"max_tool_calls": 1.5},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ProviderConfigError):
                    self.make_config(**values)

    def test_resolve_api_key_reads_environment_each_time(self) -> None:
        config = self.make_config()

        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "first"}, clear=True):
            self.assertEqual(config.resolve_api_key(), "first")
            os.environ["EXAMPLE_API_KEY"] = "second"
            self.assertEqual(config.resolve_api_key(), "second")

        self.assertNotIn("first", repr(config))
        self.assertNotIn("second", repr(config))

    def test_resolve_api_key_supports_injected_env_and_reports_missing_name(self) -> None:
        config = self.make_config()

        self.assertEqual(config.resolve_api_key({"EXAMPLE_API_KEY": "value"}), "value")
        with self.assertRaisesRegex(ProviderConfigError, "EXAMPLE_API_KEY"):
            config.resolve_api_key({})
        with self.assertRaises(ProviderConfigError):
            config.resolve_api_key({"EXAMPLE_API_KEY": "   "})

    def test_configured_api_key_is_not_exposed_by_provider_config_repr(self) -> None:
        config = self.make_config(
            api_key_env=None,
            api_key_source=ConfiguredApiKey("test-local-key-7xK2")
        )

        self.assertEqual(config.resolve_api_key({}), "test-local-key-7xK2")
        self.assertNotIn("test-local-key-7xK2", repr(config))
        self.assertIn("configured (...7xK2)", config.key_status)

    def test_model_profile_resolver_applies_profile_agent_limits(self) -> None:
        profile = ModelProfile(
            name="fast",
            provider=self.make_config(model="api-fast"),
            context_window=128_000,
            max_output_tokens=16_384,
            max_agent_rounds=7,
            max_tool_calls=17,
            max_tool_calls_per_round=3,
        )

        selected = ModelProfileResolver({"fast": profile}, default_name="fast").select(None)

        self.assertIs(selected, profile)
        self.assertEqual(selected.provider.model, "api-fast")
        self.assertEqual(selected.max_agent_rounds, 7)
        with self.assertRaisesRegex(ProviderConfigError, "unknown model"):
            ModelProfileResolver({"fast": profile}, default_name="fast").select("missing")


if __name__ == "__main__":
    unittest.main()
