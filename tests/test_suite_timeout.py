from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


class SuiteTimeoutTests(unittest.TestCase):
    def _probe(self, source: str, timeout: float = 1):
        with tempfile.TemporaryDirectory(prefix="timeout-probe-", dir=ROOT) as temporary:
            suite = Path(temporary)
            (suite / "test_probe.py").write_text(source, encoding="utf-8")
            command = [sys.executable, "-c",
                       "from pathlib import Path; from scripts.run_tests import run_test_suites; "
                       f"raise SystemExit(run_test_suites(Path.cwd(), (Path({str(suite)!r}),), {timeout}))"]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    timeout=20, env=dict(os.environ, GITHUB_ACTIONS="true"))
            pid_file = suite / "child.pid"
            child = int(pid_file.read_text()) if pid_file.exists() else None
            return result, child

    def test_hang_reports_test_stack_and_kills_descendant(self):
        result, child = self._probe('''import unittest, subprocess, sys, time
from pathlib import Path
class Hang(unittest.TestCase):
    def test_hang(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        Path(__file__).with_name('child.pid').write_text(str(child.pid))
        time.sleep(60)
''')
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertIn("timeout:test_probe.Hang.test_hang", result.stdout)
        self.assertIn("cleanup=confirmed", result.stdout)
        self.assertIn("test_probe.py", result.stderr)
        self.assertIsNotNone(child)
        self.assertFalse(psutil.pid_exists(child))

    def test_success_also_cleans_lingering_descendant(self):
        result, child = self._probe("import unittest, subprocess, sys\nfrom pathlib import Path\n"
            "class Case(unittest.TestCase):\n    def test_spawn(self):\n"
            "        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "        Path(__file__).with_name('child.pid').write_text(str(child.pid))\n", 5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(child)
        self.assertFalse(psutil.pid_exists(child))

    def test_discovery_hang_is_bounded(self):
        result, _ = self._probe("import time; time.sleep(60)")
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertIn("discovery or fixture", result.stdout)
        self.assertIn("test_probe.py", result.stderr)

    def test_normal_success_and_failure_preserve_status(self):
        for assertion, code in (("pass", 0), ("self.fail('expected')", 1)):
            with self.subTest(code=code):
                result, _ = self._probe("import unittest\nclass Case(unittest.TestCase):\n"
                                        f"    def test_case(self): {assertion}\n", 5)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertNotIn("tests=timeout:", result.stdout)

    def test_completion_during_diagnostic_grace_still_fails(self):
        result, _ = self._probe("import unittest, time\nclass Case(unittest.TestCase):\n"
                                "    def test_slow(self): time.sleep(.8)\n", .3)
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertIn("tests=timeout:", result.stdout)

    def test_invalid_timeout_rejected(self):
        for value in ("0", "-1", "nan", "inf"):
            result = subprocess.run([sys.executable, "scripts/run_tests.py", "--suite-timeout", value],
                                    cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
