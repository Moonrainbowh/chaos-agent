import unittest

from code_agent.skills.requests import resolve_skill_request


class SkillRequestTests(unittest.TestCase):
    class Skills:
        known = {"paper-review", "report-writing", "salt-cavern-simulation"}

        def info(self, identifier: str):
            if identifier not in self.known:
                raise KeyError(identifier)
            return identifier

    def test_explicit_skill_prefix(self) -> None:
        request = resolve_skill_request(
            "/skill paper-review 审查这篇论文", self.Skills()
        )
        self.assertEqual(request.skill_ids, ("paper-review",))
        self.assertEqual(request.prompt, "审查这篇论文")

    def test_direct_skill_syntax(self) -> None:
        request = resolve_skill_request(
            ":salt-cavern-simulation 运行模拟", self.Skills()
        )
        self.assertEqual(request.skill_ids, ("salt-cavern-simulation",))

    def test_composed_explicit_request(self) -> None:
        request = resolve_skill_request(
            "/skill salt-cavern-simulation + report-writing 整理报告", self.Skills()
        )
        self.assertEqual(
            request.skill_ids, ("salt-cavern-simulation", "report-writing")
        )
        self.assertEqual(request.prompt, "整理报告")

    def test_composed_prompt_preserves_internal_whitespace(self) -> None:
        request = resolve_skill_request(
            "/skill salt-cavern-simulation + report-writing   第一行\n第二行  ",
            self.Skills(),
        )
        self.assertEqual(request.prompt, "第一行\n第二行")

    def test_duplicate_skill_is_rejected(self) -> None:
        self.assertIsNone(
            resolve_skill_request(
                "/skill paper-review + paper-review 再审一次", self.Skills()
            )
        )

    def test_management_commands_and_natural_language_are_not_requests(self) -> None:
        skills = self.Skills()
        self.assertIsNone(resolve_skill_request("/skill list", skills))
        self.assertIsNone(resolve_skill_request("分析我的实验数据", skills))
        self.assertIsNone(resolve_skill_request("/unknown 任务", skills))
