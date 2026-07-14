from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.verification.models import VerificationKind, VerificationRequest, VerificationUnavailable
from code_agent.verification.python_adapter import PythonVerificationAdapter


class VerificationRequestTests(unittest.TestCase):
    def test_rejects_outside_paths_and_unbounded_timeout(self) -> None:
        with self.assertRaises(ValueError):
            VerificationRequest(VerificationKind.PYTEST, cwd="../outside")
        with self.assertRaises(ValueError):
            VerificationRequest(VerificationKind.PYTEST, targets=("C:/outside",))
        with self.assertRaises(ValueError):
            VerificationRequest(VerificationKind.PYTEST, timeout_s=901)


class PythonVerificationAdapterTests(unittest.TestCase):
    def test_unittest_builds_fixed_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = PythonVerificationAdapter(Path(temporary)).build(VerificationRequest(VerificationKind.PYTHON_UNITTEST, cwd=".", targets=("tests",)))
        self.assertEqual(command.argv, (sys.executable, "-m", "unittest", "discover", "-s", "tests"))

    def test_build_is_unavailable_without_local_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("code_agent.verification.python_adapter.importlib.util.find_spec", return_value=None):
                result = PythonVerificationAdapter(Path(temporary)).build(VerificationRequest(VerificationKind.PYTHON_BUILD))
        self.assertIsInstance(result, VerificationUnavailable)
        self.assertEqual(result.reason, "python build module is unavailable")

    def test_never_accepts_arbitrary_shell_or_environment_fields(self) -> None:
        with self.assertRaises(TypeError):
            VerificationRequest(VerificationKind.PYTEST, command="echo unsafe")  # type: ignore[call-arg]
