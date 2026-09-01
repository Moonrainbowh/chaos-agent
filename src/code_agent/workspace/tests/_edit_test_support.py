from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard


class WorkspaceEditorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()
