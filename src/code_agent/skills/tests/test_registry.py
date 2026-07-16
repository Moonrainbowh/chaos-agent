from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from code_agent.skills.registry import SkillActivation, SkillRegistry

class SkillRegistryTests(unittest.TestCase):
    def test_workspace_skill_requires_activation_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); skill = root / ".agents" / "skills" / "review"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\nUse focused checks.", encoding="utf-8")
            activation = SkillActivation(SkillRegistry.discover(root), max_chars=100)
            with self.assertRaises(PermissionError): activation.activate("review")
            activation.activate("review", approved=True)
            self.assertIn("Use focused checks.", activation.render())

    def test_discovery_merges_matching_digest_and_isolates_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); user = root / "user"; workspace = root / ".agents" / "skills"
            for base, content in ((user / "same", "same"), (workspace / "same", "same"), (user / "conflict", "one"), (workspace / "conflict", "two")):
                base.mkdir(parents=True, exist_ok=True); (base / "SKILL.md").write_text(content, encoding="utf-8")
            registry = SkillRegistry.discover(root, user)

            self.assertEqual([item.identifier for item in registry.list()], ["same"])
            self.assertEqual(len(registry.get("same").sources), 2)
            self.assertIn("conflict: conflicting digests", registry.errors())

if __name__ == "__main__": unittest.main()
