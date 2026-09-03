from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.config.loader import LocalConfigError, load_runtime_config


def _environment(directory: str, **values: str) -> dict[str, str]:
    return {
        "CHAOS_CONFIG": str(Path(directory) / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test-model",
        "CHAOS_API_KEY_ENV": "TEST_KEY",
        **values,
    }


class CapabilityStrategyConfigTests(unittest.TestCase):
    def test_defaults_to_recommended_hybrid_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = load_runtime_config(env=_environment(directory))

        self.assertIs(runtime.capability_strategy, CapabilityStrategy.HYBRID)

    def test_environment_selects_each_ab_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for strategy in CapabilityStrategy:
                with self.subTest(strategy=strategy.value):
                    runtime = load_runtime_config(
                        env=_environment(
                            directory,
                            CHAOS_CAPABILITY_STRATEGY=strategy.value,
                        )
                    )
                    self.assertIs(runtime.capability_strategy, strategy)

    def test_toml_selects_progressive_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[default]
provider = "test"
[providers.test]
api = "responses"
base_url = "https://api.example.test"
model = "test-model"
api_key_env = "TEST_KEY"
context_window = 1000
max_output_tokens = 100
[agent]
capability_strategy = "progressive"
""".strip(),
                encoding="utf-8",
            )
            runtime = load_runtime_config(env={"CHAOS_CONFIG": str(path)})

        self.assertIs(runtime.capability_strategy, CapabilityStrategy.PROGRESSIVE)

    def test_chaos_environment_overrides_legacy_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = load_runtime_config(
                env=_environment(
                    directory,
                    CHAOS_CAPABILITY_STRATEGY="progressive",
                    CODE_AGENT_CAPABILITY_STRATEGY="legacy",
                )
            )

        self.assertIs(runtime.capability_strategy, CapabilityStrategy.PROGRESSIVE)

    def test_rejects_unknown_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                LocalConfigError,
                "legacy, hybrid, or progressive",
            ):
                load_runtime_config(
                    env=_environment(
                        directory,
                        CHAOS_CAPABILITY_STRATEGY="automatic",
                    )
                )


if __name__ == "__main__":
    unittest.main()
