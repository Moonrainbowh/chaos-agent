from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.projects.discovery import ProjectKind, discover_projects
from code_agent.workspace.paths import WorkspacePathGuard


class ProjectDiscoveryTests(unittest.TestCase):
    def test_discovers_deterministic_python_node_and_dotnet_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            node = root / "web"; node.mkdir(); (node / "node_modules").mkdir(); (node / "package.json").write_text('{"scripts":{"test":"vitest","build":"vite"}}', encoding="utf-8")
            dotnet = root / "dotnet"; dotnet.mkdir(); (dotnet / "app.csproj").write_text("<Project />", encoding="utf-8")
            candidates = discover_projects(root, WorkspacePathGuard(root), lambda executable: f"C:/{executable}")
            self.assertEqual([candidate.kind for candidate in candidates], [ProjectKind.PYTHON, ProjectKind.DOTNET, ProjectKind.NODE])
            self.assertIn("--no-restore", candidates[1].recipes[0].argv)
            self.assertNotIn("npx", " ".join(candidates[-1].recipes[0].argv))

    def test_unavailable_tools_are_reported_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            candidate = discover_projects(root, WorkspacePathGuard(root), lambda _: None)[0]
            self.assertFalse(candidate.recipes[1].available)
            self.assertIn("unavailable", candidate.recipes[1].reason)
