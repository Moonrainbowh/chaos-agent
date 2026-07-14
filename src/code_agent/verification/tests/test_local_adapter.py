from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.verification.models import VerificationKind, VerificationRequest, VerificationUnavailable


class LocalVerificationAdapterTests(unittest.TestCase):
    def test_node_uses_only_declared_script_with_existing_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "node_modules").mkdir()
            (root / "package.json").write_text('{"scripts":{"test":"node test.js"}}', encoding="utf-8")
            with patch("code_agent.verification.local_adapter.shutil.which", return_value="npm.exe"):
                command = LocalVerificationAdapter(root).build(VerificationRequest(VerificationKind.NODE_TEST))
        self.assertEqual(command.argv, ("npm", "run", "test", "--", "--offline"))

    def test_node_and_dotnet_unavailable_paths_never_fall_back_to_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = LocalVerificationAdapter(root).build(VerificationRequest(VerificationKind.NODE_BUILD))
            dotnet = LocalVerificationAdapter(root).build(VerificationRequest(VerificationKind.DOTNET_TEST))
        self.assertIsInstance(node, VerificationUnavailable)
        self.assertIsInstance(dotnet, VerificationUnavailable)

    def test_dotnet_uses_no_restore_and_rejects_non_project_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.csproj").write_text("<Project />", encoding="utf-8")
            with patch("code_agent.verification.local_adapter.shutil.which", return_value="dotnet.exe"):
                command = LocalVerificationAdapter(root).build(VerificationRequest(VerificationKind.DOTNET_BUILD))
                invalid = LocalVerificationAdapter(root).build(VerificationRequest(VerificationKind.DOTNET_TEST, targets=("note.txt",)))
        self.assertEqual(command.argv, ("dotnet", "build", "--no-restore"))
        self.assertIsInstance(invalid, VerificationUnavailable)
