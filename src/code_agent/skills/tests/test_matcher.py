import unittest

from code_agent.skills.matcher import match_skill
from code_agent.skills.registry import SkillManifest


def skill(identifier: str, description: str) -> SkillManifest:
    return SkillManifest(identifier, description, "test", "digest", description, True)


class SkillMatcherTests(unittest.TestCase):
    def test_matches_specific_task_to_skill_identifier(self) -> None:
        result = match_skill(
            "review this academic paper",
            (
                skill("academic-paper-reviewer", "peer review and referee report"),
                skill("data-analysis", "analyze tables and measurements"),
            ),
        )
        self.assertEqual(result.identifier, "academic-paper-reviewer")
        self.assertIn("paper", result.matched_terms)

    def test_ambiguous_generic_request_stays_a_normal_prompt(self) -> None:
        result = match_skill(
            "help me analyze this",
            (
                skill("data-analysis", "analyze measurements"),
                skill("academic-paper", "academic writing"),
            ),
        )
        self.assertIsNone(result)

    def test_description_only_match_can_reach_high_confidence(self) -> None:
        result = match_skill(
            "review academic manuscripts",
            (skill("manuscript-helper", "review academic manuscripts"),),
        )
        self.assertEqual(result.identifier, "manuscript-helper")

    def test_unrelated_prompt_does_not_match(self) -> None:
        self.assertIsNone(
            match_skill(
                "what is the weather today",
                (skill("paper-review", "review academic manuscripts"),),
            )
        )

    def test_exclusion_text_is_not_a_trigger(self) -> None:
        self.assertIsNone(
            match_skill(
                "review code",
                (skill("figure", "scientific figures; do not use for code debugging"),),
            )
        )
        self.assertIsNone(
            match_skill(
                "code debugging",
                (skill("figure", "scientific figures; avoid code debugging"),),
            )
        )
