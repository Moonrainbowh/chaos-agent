from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from code_agent.skills.registry import SkillActivation, SkillRegistry

class SkillRegistryTests(unittest.TestCase):
    def test_workspace_skill_requires_activation_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); skill = root / ".chaos-agent" / "skills" / "review"; skill.mkdir(parents=True)
            (skill / "skill.toml").write_text('[skill]\nid = "review"\ndescription = "Review code"', encoding="utf-8")
            (skill / "SKILL.md").write_text("Use focused checks.", encoding="utf-8")
            activation = SkillActivation(SkillRegistry.discover(root), max_chars=100)
            with self.assertRaises(PermissionError): activation.activate("review")
            activation.activate("review", approved=True)
            self.assertIn("Use focused checks.", activation.render())

if __name__ == "__main__": unittest.main()
