from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.config.loader import (  # noqa: E402
    LocalConfigError,
    default_config_path,
    load_runtime_config,
    resolve_config_path,
)


class LocalApiConfigTests(unittest.TestCase):
    def test_loads_optional_token_pricing_as_a_complete_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "priced"
[providers.priced]
api = "responses"
base_url = "https://api.example.test"
model = "priced-model"
api_key_env = "KEY"
context_window = 1000
max_output_tokens = 100
input_cost_per_million = 0.4
output_cost_per_million = 1.6
""".strip(),
                encoding="utf-8",
            )

            runtime = load_runtime_config(env={"CHAOS_CONFIG": str(path)})

        profile = runtime.profiles[0]
        self.assertEqual(profile.input_cost_per_million, 0.4)
        self.assertEqual(profile.output_cost_per_million, 1.6)

    def test_prefers_chaos_configuration_and_falls_back_to_legacy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            env = {"LOCALAPPDATA": str(base)}
            legacy = base / "code-agent" / "config.toml"
            legacy.parent.mkdir()
            legacy.write_text("[default]", encoding="utf-8")

            self.assertEqual(default_config_path(env), base / "chaos-agent" / "config.toml")
            self.assertEqual(resolve_config_path(env), legacy)

            current = base / "chaos-agent" / "config.toml"
            current.parent.mkdir()
            current.write_text("[default]", encoding="utf-8")
            self.assertEqual(resolve_config_path(env), current)

    def test_chaos_environment_variables_override_legacy_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = load_runtime_config(
                env={
                    "CHAOS_CONFIG": str(Path(directory) / "missing.toml"),
                    "CHAOS_API": "responses",
                    "CODE_AGENT_API": "chat_completions",
                    "CHAOS_MODEL": "chaos-model",
                    "CODE_AGENT_MODEL": "legacy-model",
                    "CHAOS_BASE_URL": "https://chaos.example.test",
                    "CHAOS_API_KEY_ENV": "CHAOS_KEY",
                    "CODE_AGENT_API_KEY_ENV": "LEGACY_KEY",
                }
            )

        self.assertEqual(runtime.provider.api.value, "responses")
        self.assertEqual(runtime.provider.model, "chaos-model")
        self.assertEqual(runtime.provider.key_status, "environment (CHAOS_KEY)")
        self.assertEqual(runtime.approval_mode.value, "auto")

    def test_loads_default_provider_with_masked_local_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "openai"

[providers.openai]
api = "responses"
base_url = "https://api.openai.com"
model = "gpt-4.1-mini"
api_key = "test-local-key-7xK2"
context_window = 128000
max_output_tokens = 16384
""".strip(),
                encoding="utf-8",
            )

            runtime = load_runtime_config(env={"CODE_AGENT_CONFIG": str(path)})

        self.assertEqual(runtime.provider.model, "gpt-4.1-mini")
        self.assertEqual(runtime.provider.resolve_api_key({}), "test-local-key-7xK2")
        self.assertNotIn("test-local-key-7xK2", repr(runtime))
        self.assertIn("configured (...7xK2)", runtime.key_status)

    def test_missing_file_preserves_environment_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = load_runtime_config(
                env={
                    "CODE_AGENT_CONFIG": str(Path(directory) / "missing.toml"),
                    "CODE_AGENT_API": "chat_completions",
                    "CODE_AGENT_BASE_URL": "https://api.example.test",
                    "CODE_AGENT_MODEL": "legacy-model",
                    "CODE_AGENT_API_KEY_ENV": "LEGACY_KEY",
                    "CODE_AGENT_APPROVAL_MODE": "auto",
                }
            )

        self.assertEqual(runtime.profile, "environment")
        self.assertEqual(runtime.provider.api.value, "chat_completions")
        self.assertEqual(runtime.provider.base_url, "https://api.example.test")
        self.assertEqual(runtime.provider.model, "legacy-model")
        self.assertEqual(runtime.provider.key_status, "environment (LEGACY_KEY)")
        self.assertEqual(runtime.approval_mode.value, "auto")

    def test_loads_full_local_mode_and_sensitive_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "openai"

[providers.openai]
api = "responses"
base_url = "https://api.openai.com"
model = "test-model"
api_key_env = "OPENAI_API_KEY"
context_window = 128000
max_output_tokens = 16384

[agent]
approval_mode = "full-local"
allow_sensitive_paths = true
""".strip(),
                encoding="utf-8",
            )

            runtime = load_runtime_config(env={"CODE_AGENT_CONFIG": str(path)})

        self.assertEqual(runtime.approval_mode.value, "full-local")
        self.assertTrue(runtime.allow_sensitive_paths)

    def test_cli_profile_and_existing_environment_variables_override_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "openai"

[providers.openai]
api = "responses"
base_url = "https://api.openai.com"
model = "default-model"
api_key_env = "OPENAI_KEY"
context_window = 128000
max_output_tokens = 16384

[providers.company]
api = "anthropic_messages"
base_url = "https://api.company.test"
model = "company-model"
api_key_env = "COMPANY_KEY"
context_window = 64000
max_output_tokens = 8192

[agent]
approval_mode = "plan"
""".strip(),
                encoding="utf-8",
            )
            runtime = load_runtime_config(
                env={
                    "CODE_AGENT_CONFIG": str(path),
                    "CODE_AGENT_PROFILE": "openai",
                    "CODE_AGENT_API": "chat_completions",
                    "CODE_AGENT_MODEL": "env-model",
                    "CODE_AGENT_APPROVAL_MODE": "auto",
                },
                cli_profile="company",
            )

        self.assertEqual(runtime.profile, "company")
        self.assertEqual(runtime.provider.api.value, "chat_completions")
        self.assertEqual(runtime.provider.model, "env-model")
        self.assertEqual(runtime.provider.key_status, "environment (COMPANY_KEY)")
        self.assertEqual(runtime.approval_mode.value, "auto")

    def test_rejects_ambiguous_key_source_without_echoing_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "openai"

[providers.openai]
api = "responses"
base_url = "https://api.openai.com"
model = "model"
api_key = "test-local-key-7xK2"
api_key_env = "OPENAI_KEY"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LocalConfigError, "exactly one") as raised:
                load_runtime_config(env={"CODE_AGENT_CONFIG": str(path)})

        self.assertNotIn("test-local-key-7xK2", str(raised.exception))

    def test_unselected_profile_ignores_environment_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "one"
[providers.one]
api = "responses"
base_url = "https://one.example.test"
model = "one"
api_key_env = "ONE_KEY"
context_window = 1000
max_output_tokens = 100
[providers.two]
api = "chat_completions"
base_url = "https://two.example.test"
model = "two"
api_key_env = "TWO_KEY"
context_window = 2000
max_output_tokens = 200
""".strip(), encoding="utf-8")
            runtime = load_runtime_config(env={"CHAOS_CONFIG": str(path), "CHAOS_MODEL": "override"})

        profiles = {profile.name: profile for profile in runtime.profiles}
        self.assertEqual(profiles["one"].provider.model, "override")
        self.assertEqual(profiles["two"].provider.model, "two")

    def test_parses_structured_stdio_mcp_server_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "local"
[providers.local]
api = "responses"
base_url = "https://api.example.test"
model = "test"
api_key_env = "KEY"
context_window = 1000
max_output_tokens = 100
[mcp.servers.docs]
command = "python"
args = ["server.py", "--read-only"]
cwd = "tools/docs"
environment = ["DOCS_TOKEN"]
enabled = true
approved = true
""".strip(), encoding="utf-8")
            runtime = load_runtime_config(env={"CHAOS_CONFIG": str(path)})

        server = runtime.mcp_servers[0]
        self.assertEqual(server.command, "python")
        self.assertEqual(server.args, ("server.py", "--read-only"))
        self.assertEqual(server.environment, ("DOCS_TOKEN",))


if __name__ == "__main__":
    unittest.main()
