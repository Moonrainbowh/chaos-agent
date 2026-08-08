from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_tests import discover_test_suites  # noqa: E402


class TestSuiteDiscoveryTests(unittest.TestCase):
    def test_discovers_sorted_features_and_integration_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "src/code_agent/zeta/tests/test_zeta.py",
                "src/code_agent/alpha/tests/test_alpha.py",
                "tests/test_root.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            suites = discover_test_suites(root)

        self.assertEqual(
            tuple(path.relative_to(root).as_posix() for path in suites),
            (
                "src/code_agent/alpha/tests",
                "src/code_agent/zeta/tests",
                "tests",
            ),
        )

    def test_rejects_a_feature_without_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/code_agent/alpha").mkdir(parents=True)
            integration = root / "tests/test_root.py"
            integration.parent.mkdir()
            integration.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "src/code_agent/alpha"):
                discover_test_suites(root)

    def test_rejects_nested_only_tests_that_discover_would_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "src/code_agent/alpha/tests/nested/test_alpha.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("", encoding="utf-8")
            integration = root / "tests/test_root.py"
            integration.parent.mkdir()
            integration.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "src/code_agent/alpha"):
                discover_test_suites(root)


if __name__ == "__main__":
    unittest.main()
