from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_atomic_replace  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows replacement identity semantics")
class WindowsReplaceIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_swapped_source_preserves_original_agent_and_foreign_bytes(self) -> None:
        target = self.root / "module.py"
        moved_agent = self.root / "agent-moved.py"
        target.write_bytes(b"original")
        plan = self.editor.plan_write(target.name, "agent")
        real_replace = _windows_atomic_replace._replace_file

        def swap_source(final: Path, temporary: Path, backup: Path) -> None:
            os.rename(temporary, moved_agent)
            temporary.write_bytes(b"foreign")
            real_replace(final, temporary, backup)

        with patch.object(
            _windows_atomic_replace, "_replace_file", side_effect=swap_source
        ):
            with self.assertRaises(WorkspaceError):
                self.editor.apply(plan)

        contents = {path.read_bytes() for path in self.root.iterdir()}
        self.assertTrue({b"original", b"agent", b"foreign"} <= contents)


if __name__ == "__main__":
    unittest.main()
