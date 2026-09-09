import tempfile
import unittest
from pathlib import Path

from code_agent.config.loader import _provider_config, LocalConfigError


class ProviderAuthenticationConfigTests(unittest.TestCase):
    def test_oauth_is_lazy_and_keeps_protocol_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "auth.dat"
            values = dict(provider_id="openai-codex", auth="oauth", api="codex_responses",
                          base_url="https://chatgpt.com/backend-api", model="example",
                          responses_path="/codex/responses")
            config = _provider_config(values, {"CHAOS_AUTH_FILE": str(auth)}, allow_environment=False)
            self.assertEqual(config.auth_source.path, auth)
            self.assertEqual(config.responses_path, "/codex/responses")
            self.assertFalse(auth.exists())

    def test_conflicting_key_sources_and_oauth_override_are_rejected(self):
        values = dict(provider_id="anthropic", auth="oauth", api="anthropic_messages",
                      base_url="https://api.anthropic.com", model="example")
        with self.assertRaises(LocalConfigError):
            _provider_config(dict(values, api_key="do-not-print"), {}, allow_environment=False)
        with self.assertRaises(LocalConfigError):
            _provider_config(values, {"CHAOS_API_KEY_ENV": "OVERRIDE"}, allow_environment=True)
        with self.assertRaises(LocalConfigError):
            _provider_config(dict(values, provider_id="google"), {}, allow_environment=False)

    def test_existing_custom_api_key_and_paths_remain_available(self):
        values = dict(api="responses", base_url="https://example.test/v1", model="example",
                      api_key_env="CUSTOM_KEY", responses_path="/responses")
        config = _provider_config(values, {}, allow_environment=False)
        self.assertIsNone(config.auth_source)
        self.assertEqual(config.resolve_api_key({"CUSTOM_KEY": "secret"}), "secret")
        self.assertEqual(config.responses_path, "/responses")
