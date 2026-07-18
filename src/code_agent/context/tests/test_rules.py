from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.errors import ContextError, RuleLimitError  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RuleLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cwd = self.root / "src" / "feature"
        self.cwd.mkdir(parents=True)
        self.guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            self.guard, IgnoreRules.from_workspace(self.root)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, **overrides: object) -> ContextConfig:
        values = {
            "workspace_root": self.root,
            "cwd": self.cwd,
            "system_prompt": "System",
            "max_rule_bytes": 1_000,
            "max_rules_total": 4_000,
            "repo_scan": 100,
            "repo_map_tokens": 100,
            "message_tokens": 100,
            "recent_messages": 2,
        }
        values.update(overrides)
        return ContextConfig(**values)  # type: ignore[arg-type]

    def loader(self, **overrides: object) -> RuleLoader:
        return RuleLoader(self.guard, self.files, self.config(**overrides))

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_loads_root_extensions_then_only_the_cwd_ancestor_chain(self) -> None:
        self.write("AGENTS.md", "root")
        self.write("AGENTS.zeta.md", "zeta")
        self.write("AGENTS.python.md", "python")
        self.write("src/AGENTS.md", "source")
        self.write("src/feature/AGENTS.md", "feature")
        self.write("src/sibling/AGENTS.md", "sibling")

        rules = self.loader().load()

        self.assertEqual(
            [rule.path for rule in rules],
            [
                "AGENTS.md",
                "AGENTS.python.md",
                "AGENTS.zeta.md",
                "src/AGENTS.md",
                "src/feature/AGENTS.md",
            ],
        )
        self.assertEqual([rule.scope_depth for rule in rules], [0, 0, 0, 1, 2])
        self.assertEqual([rule.content for rule in rules], [
            "root", "python", "zeta", "source", "feature"
        ])
        self.assertEqual(sum(rule.path == "AGENTS.md" for rule in rules), 1)

    def test_root_extension_discovery_does_not_enumerate_the_workspace(self) -> None:
        self.write("AGENTS.python.md", "python")

        with patch.object(
            self.files,
            "list_files",
            side_effect=AssertionError("recursive workspace listing is forbidden"),
        ):
            rules = self.loader().load()

        self.assertEqual([rule.path for rule in rules], ["AGENTS.python.md"])

    def test_unchanged_root_extension_names_are_discovered_once_per_loader(self) -> None:
        self.write("AGENTS.python.md", "python")
        loader = self.loader()

        with patch.object(
            loader,
            "_discover_root_extensions",
            wraps=loader._discover_root_extensions,
        ) as discover:
            first = loader.load()
            second = loader.load()

        self.assertEqual(first, second)
        self.assertEqual(discover.call_count, 1)

    def test_missing_rule_files_are_skipped_and_render_has_path_boundaries(self) -> None:
        self.write("src/feature/AGENTS.md", "Use the local contract.")

        rules = self.loader().load()
        rendered = self.loader().render(rules)

        self.assertEqual([rule.path for rule in rules], ["src/feature/AGENTS.md"])
        self.assertIn('[PROJECT_RULE path="src/feature/AGENTS.md" depth=2]', rendered)
        self.assertIn("Use the local contract.", rendered)
        self.assertIn("[/PROJECT_RULE]", rendered)

    def test_rejects_a_cwd_outside_the_guarded_workspace(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir(exist_ok=True)
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)

        with self.assertRaises(ContextError):
            self.loader().load(outside)

    def test_enforces_per_file_byte_limit(self) -> None:
        self.write("AGENTS.md", "12345")

        with self.assertRaises(RuleLimitError):
            self.loader(max_rule_bytes=4).load()

    def test_enforces_total_rule_byte_limit_without_partial_results(self) -> None:
        self.write("AGENTS.md", "12345")
        self.write("AGENTS.python.md", "67890")

        with self.assertRaises(RuleLimitError):
            self.loader(max_rule_bytes=5, max_rules_total=9).load()
        self.assertEqual(
            len(self.loader(max_rule_bytes=5, max_rules_total=10).load()), 2
        )


if __name__ == "__main__":
    unittest.main()
