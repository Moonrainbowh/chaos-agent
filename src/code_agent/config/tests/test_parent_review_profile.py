from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.config.loader import LocalConfigError, load_runtime_config  # noqa: E402


CONFIG = """
[default]
provider = "main"
[providers.main]
api = "responses"
base_url = "https://main.example.test"
model = "main-model"
api_key_env = "MAIN_KEY"
context_window = 1000
max_output_tokens = 100
[providers.review]
api = "chat_completions"
base_url = "https://review.example.test"
model = "review-model"
api_key = "review-secret-7xK2"
context_window = 2000
max_output_tokens = 200
"""


class ParentReviewProfileConfigTests(unittest.TestCase):
    def load(self, agent: str = "", **environment: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(CONFIG + agent, encoding="utf-8")
            return load_runtime_config(env={"CHAOS_CONFIG": str(path), **environment})

    def test_absent_setting_defaults_to_none_without_environment_override(self) -> None:
        for agent in ("", '[agent]\napproval_mode = "auto"\n'):
            with self.subTest(agent=agent):
                runtime = self.load(
                    agent,
                    CHAOS_PARENT_REVIEW_PROFILE="review",
                    CODE_AGENT_PARENT_REVIEW_PROFILE="review",
                )
                self.assertIsNone(runtime.parent_review_profile)
                self.assertEqual(runtime.profile, "main")

    def test_missing_file_defaults_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(Path(directory) / "missing.toml"),
                "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://example.test",
                "CHAOS_MODEL": "environment-model",
                "CHAOS_API_KEY_ENV": "KEY",
            })
        self.assertIsNone(runtime.parent_review_profile)

    def test_configured_review_preserves_default_and_other_profiles(self) -> None:
        baseline = self.load(CHAOS_MODEL="overridden-main")
        runtime = self.load(
            '[agent]\nparent_review_profile = "review"\n',
            CHAOS_MODEL="overridden-main",
        )
        self.assertEqual(runtime.parent_review_profile, "review")
        self.assertEqual(runtime.profile, baseline.profile)
        self.assertEqual(runtime.provider, baseline.provider)
        self.assertEqual(runtime.profiles, baseline.profiles)
        profiles = {profile.name: profile for profile in runtime.profiles}
        self.assertEqual(profiles["main"].provider.model, "overridden-main")
        self.assertEqual(profiles["review"].provider.model, "review-model")

    def test_selected_profile_is_valid_review_profile(self) -> None:
        runtime = self.load('[agent]\nparent_review_profile = "main"\n')
        self.assertEqual(runtime.parent_review_profile, "main")

    def test_invalid_values_fail_without_echoing_secrets(self) -> None:
        for value in ('""', '"   "', '"missing"', '" review "', '"review-secret-7xK2"', 'true', '42', '["review"]', '{name = "review"}'):
            with self.subTest(value=value):
                with self.assertRaises(LocalConfigError) as raised:
                    self.load(f"[agent]\nparent_review_profile = {value}\n")
                message = str(raised.exception)
                self.assertIn("agent.parent_review_profile", message)
                self.assertNotIn("review-secret-7xK2", message)


if __name__ == "__main__":
    unittest.main()
