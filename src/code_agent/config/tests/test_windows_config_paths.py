from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import load_runtime_config
from code_agent.workspace.errors import WindowsLongPathError
from code_agent.workspace.windows_paths import windows_path_support


@unittest.skipUnless(os.name == "nt", "Windows path limits are Windows-only")
class WindowsConfigPathTests(unittest.TestCase):
    def tearDown(self) -> None:
        windows_path_support.cache_clear()

    def test_explicit_long_config_is_not_silently_treated_as_missing(self) -> None:
        config = Path("C:/") / ("c" * 240) / "config.toml"
        env = {
            "CHAOS_CONFIG": str(config),
            "CHAOS_API": "responses",
            "CHAOS_BASE_URL": "https://api.example.test",
            "CHAOS_MODEL": "test",
            "CHAOS_API_KEY_ENV": "KEY",
        }

        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            windows_path_support.cache_clear()
            with self.assertRaisesRegex(WindowsLongPathError, "configuration"):
                load_runtime_config(env=env)


if __name__ == "__main__":
    unittest.main()
