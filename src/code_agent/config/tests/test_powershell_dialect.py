from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.config.loader import LocalConfigError, load_runtime_config
from code_agent.runtime.models import ShellDialect


def _environment(directory: str, **values: str) -> dict[str, str]:
    return {
        "CHAOS_CONFIG": str(Path(directory) / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test-model",
        "CHAOS_API_KEY_ENV": "TEST_KEY",
        **values,
    }


class PowerShellDialectConfigTests(unittest.TestCase):
    def test_missing_and_case_insensitive_auto_select_automatic_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                load_runtime_config(env=_environment(directory)).powershell_dialect
            )
            for value in ("auto", "AUTO", "Auto"):
                with self.subTest(value=value):
                    config = load_runtime_config(
                        env=_environment(
                            directory, CHAOS_POWERSHELL_DIALECT=value
                        )
                    )
                    self.assertIsNone(config.powershell_dialect)

    def test_environment_accepts_both_explicit_dialects(self) -> None:
        for value, expected in (
            ("powershell_7", ShellDialect.POWERSHELL_7),
            ("windows_powershell_5_1", ShellDialect.WINDOWS_POWERSHELL_5_1),
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                config = load_runtime_config(
                    env=_environment(directory, CHAOS_POWERSHELL_DIALECT=value)
                )
                self.assertIs(config.powershell_dialect, expected)

    def test_chaos_environment_name_overrides_legacy_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_runtime_config(
                env=_environment(
                    directory,
                    CHAOS_POWERSHELL_DIALECT="AUTO",
                    CODE_AGENT_POWERSHELL_DIALECT="powershell_7",
                )
            )
        self.assertIsNone(config.powershell_dialect)

    def test_toml_value_is_loaded_and_environment_can_override_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "local"
[providers.local]
api = "responses"
base_url = "https://api.example.test"
model = "test-model"
api_key_env = "TEST_KEY"
context_window = 1000
max_output_tokens = 100
[agent]
powershell_dialect = "windows_powershell_5_1"
""".strip(),
                encoding="utf-8",
            )
            config = load_runtime_config(env={"CHAOS_CONFIG": str(path)})
            overridden = load_runtime_config(
                env={
                    "CHAOS_CONFIG": str(path),
                    "CHAOS_POWERSHELL_DIALECT": "powershell_7",
                }
            )
        self.assertIs(
            config.powershell_dialect, ShellDialect.WINDOWS_POWERSHELL_5_1
        )
        self.assertIs(overridden.powershell_dialect, ShellDialect.POWERSHELL_7)

    def test_invalid_and_non_powershell_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for value in ("posix_sh", "bash", ""):
                with self.subTest(value=value):
                    with self.assertRaises(LocalConfigError):
                        load_runtime_config(
                            env=_environment(
                                directory, CHAOS_POWERSHELL_DIALECT=value
                            )
                        )


if __name__ == "__main__":
    unittest.main()
