from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.subject import snapshot_subject


class SubjectSnapshotTests(unittest.TestCase):
    def test_same_subject_is_deterministic_and_write_changes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "note.py").write_text("before", encoding="utf-8")
            guard = WorkspacePathGuard(root)
            first = snapshot_subject(guard, 1, ("note.py",))
            self.assertEqual(first, snapshot_subject(guard, 1, ("note.py",)))
            (root / "note.py").write_text("after", encoding="utf-8")
            self.assertNotEqual(first.subject_hash, snapshot_subject(guard, 2, ("note.py",)).subject_hash)

    def test_guard_and_limits_reject_sensitive_or_unbounded_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / ".env").write_text("secret", encoding="utf-8")
            guard = WorkspacePathGuard(root)
            with self.assertRaises(Exception):
                snapshot_subject(guard, 1, (".env",))
            with self.assertRaises(ValueError):
                snapshot_subject(guard, 1, ("missing",), max_files=0)
