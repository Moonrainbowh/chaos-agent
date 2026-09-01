from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace._git_environment import isolated_git_environment  # noqa: E402


class GitOptionalLockTests(unittest.TestCase):
    def test_agent_git_processes_disable_optional_background_locks(self) -> None:
        environment = isolated_git_environment(
            {"PATH": "fixed", "GIT_OPTIONAL_LOCKS": "1"}
        )

        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")


if __name__ == "__main__":
    unittest.main()
