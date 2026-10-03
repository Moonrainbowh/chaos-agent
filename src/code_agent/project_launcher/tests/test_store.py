from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from code_agent.project_launcher.store import ProjectStore, ProjectStoreError


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "state" / "projects.json"
        self.store = ProjectStore(self.path)
        self.first = self.root / "中文工程"
        self.second = self.root / "other"
        self.first.mkdir()
        self.second.mkdir()

    def test_restart_retains_selection_and_recent_order(self):
        self.store.add(self.first)
        self.store.add(self.second)
        self.assertEqual(self.store.select(self.second), self.second)
        restarted = ProjectStore(self.path)
        self.assertEqual(restarted.last_root(), self.second)
        self.assertEqual([row.root for row in restarted.entries()], [self.second, self.first])
        self.assertIn("中文工程", self.path.read_text(encoding="utf-8"))

    def test_canonical_path_and_windows_case_deduplicate(self):
        first = self.store.add(self.first)
        other = self.store.add(self.first / ".." / self.first.name)
        self.assertEqual(first.identifier, other.identifier)
        if os.name == "nt":
            self.store.add(Path(str(self.first).upper()))
        self.assertEqual(len(self.store.entries()), 1)

    def test_invalid_directory_and_unregistered_selection_do_not_create_state(self):
        for root in (Path("relative"), self.root / "missing"):
            with self.assertRaises(ProjectStoreError):
                self.store.add(root)
        with self.assertRaises(ProjectStoreError):
            self.store.select(self.first)
        with self.assertRaises(ProjectStoreError):
            ProjectStore(Path("projects.json"))
        self.assertFalse(self.path.exists())

    def test_missing_registered_directory_remains_visible_but_cannot_select(self):
        self.store.add(self.first)
        self.store.select(self.first)
        self.first.rmdir()
        row = self.store.entries()[0]
        self.assertFalse(row.available)
        with self.assertRaises(ProjectStoreError):
            self.store.select(self.first)

    def test_remove_preserves_engineering_files_and_blocks_history_seed(self):
        self.store.seed((self.first, self.second))
        self.store.select(self.first)
        artifact = self.first / "data.txt"
        artifact.write_text("kept", encoding="utf-8")
        self.store.remove(self.first)
        self.assertEqual(artifact.read_text(), "kept")
        self.assertIsNone(self.store.last_root())
        restarted = ProjectStore(self.path)
        restarted.seed((self.first,))
        self.assertEqual([row.root for row in restarted.entries()], [self.second])
        restarted.add(self.first)
        self.assertEqual(len(restarted.entries()), 2)

    def test_seed_is_available_absolute_only_and_does_not_change_recent(self):
        self.store.add(self.first)
        self.store.select(self.first)
        self.store.seed((Path("relative"), self.root / "missing", self.second, self.first))
        self.assertEqual(self.store.last_root(), self.first)
        self.assertEqual(len(self.store.entries()), 2)

    def test_bad_state_is_preserved_for_every_write_operation(self):
        self.path.parent.mkdir()
        for raw in (b"{broken", b"\xff", json.dumps({"version": 1, "projects": ["relative"],
                           "removed": [], "recent": None}).encode()):
            self.path.write_bytes(raw)
            for operation in (lambda: self.store.add(self.first), lambda: self.store.seed((self.first,)),
                              lambda: self.store.remove(self.first), lambda: self.store.select(self.first)):
                with self.assertRaises(ProjectStoreError):
                    operation()
                self.assertEqual(self.path.read_bytes(), raw)

    def test_limit_rejects_explicit_add_and_bounds_seed(self):
        roots = []
        for index in range(65):
            root = self.root / f"p{index}"
            root.mkdir()
            roots.append(root)
        self.store.seed(roots)
        self.assertEqual(len(self.store.entries()), 64)
        before = self.path.read_bytes()
        with self.assertRaises(ProjectStoreError):
            self.store.add(roots[-1])
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_original_and_cleans_temp(self):
        self.store.add(self.first)
        before = self.path.read_bytes()
        with patch("code_agent.project_launcher.store.os.replace", side_effect=OSError("failed")):
            with self.assertRaises(ProjectStoreError):
                self.store.add(self.second)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".projects-*")), [])

    def test_browser_anchors_and_shallow_children(self):
        self.store.add(self.first)
        hidden = self.first / ".git"
        hidden.mkdir()
        child = self.first / "工程子目录"
        child.mkdir()
        (child / "nested").mkdir()
        (self.first / "file.txt").write_text("not dir")
        self.assertIn(self.first, self.store.browse_roots())
        self.assertIn(Path.home().resolve(), self.store.browse_roots())
        self.assertEqual(self.store.child_directories(self.first), (child,))

    def test_browser_children_bound(self):
        for index in range(1002):
            (self.first / f"p{index}").mkdir()
        self.assertEqual(len(self.store.child_directories(self.first)), 1000)
