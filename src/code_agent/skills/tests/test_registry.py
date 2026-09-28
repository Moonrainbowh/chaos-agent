from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from code_agent.skills.registry import SkillActivation, SkillRegistry

class SkillRegistryTests(unittest.TestCase):
    def test_frontmatter_reads_bounded_capability_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); skill = root / ".agents" / "skills" / "review"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review code\nrequires:\n  - read_workspace\n  - python_analysis\n  - read_workspace\n---\nUse focused checks.",
                encoding="utf-8",
            )
            manifest = SkillRegistry.discover(root).get("review")
            self.assertEqual(manifest.requires, ("read_workspace", "python_analysis"))

    def test_requires_does_not_discard_multiline_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); skill = root / ".agents" / "skills" / "review"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: |\n  Review\n  source code\nrequires:\n  - read_workspace\n---\nUse focused checks.",
                encoding="utf-8",
            )
            self.assertEqual(SkillRegistry.discover(root).get("review").description, "Review source code")

    def test_requires_accepts_inline_and_unindented_lists(self) -> None:
        for declaration in ("requires: [read_workspace, python_analysis]", "requires: python_analysis", "requires:\n- read_workspace"):
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); skill = root / ".agents" / "skills" / "review"; skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    f"---\nname: review\n{declaration}\n---\nUse focused checks.", encoding="utf-8"
                )
                self.assertTrue(SkillRegistry.discover(root).get("review").requires)

    def test_indented_requires_text_in_description_is_not_a_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); skill = root / ".agents" / "skills" / "review"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: |\n  summary\n  requires: prose only\n  tail\n---\nUse focused checks.",
                encoding="utf-8",
            )
            manifest = SkillRegistry.discover(root).get("review")
            self.assertEqual(manifest.requires, ())
            self.assertIn("requires: prose only", manifest.description)

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
