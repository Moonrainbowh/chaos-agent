import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from code_agent.interfaces.tui_skill_commands import handle_skill_command
from code_agent.interfaces.tui_run import submit
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.interfaces.command_registry import REGISTRY


class DirectSkillDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enabled_skills: list[str] = []
        self.submitted_prompts: list[str] = []
        self.rows: list[tuple[DisplayKind, str]] = []

        class FakeSkill:
            def __init__(self, identifier: str, description: str):
                self.identifier = identifier
                self.description = description
                self.digest = "d123"

        skills_dict = {
            "academic-paper": FakeSkill("academic-paper", "Write and revise academic papers"),
            "cheat-seed": FakeSkill("cheat-seed", "Brainstorm viral topic angles"),
            "report-writing": FakeSkill("report-writing", "Write an experiment report"),
        }

        test_self = self

        class FakeSkillsController:
            active_skills: set[str] = set()

            def list(self):
                return tuple(skills_dict.values())

            def info(self, ident: str):
                if ident in skills_dict:
                    return skills_dict[ident]
                raise KeyError(ident)

            async def enable(self, thread_id: str, ident: str):
                test_self.enabled_skills.append(ident)
                self.active_skills.add(ident)

            def activation(self, thread_id: str):
                return SimpleNamespace(active=lambda: [skills_dict[ident] for ident in self.active_skills])

            def sources(self, ident: str):
                return ("local",)

        class FakeInput:
            text = ""
            def replace(self, val: str):
                self.text = val

        class FakeApp:
            skills = FakeSkillsController()
            current_thread_id = "test-thread"
            command_registry = REGISTRY
            attachment_draft = None
            _pending_approval = None
            _peer_run_task = None
            _run_task = None
            _starting_task = False
            input = FakeInput()
            tasks = None
            active_task_id = None
            _closing = False
            state = TerminalState()

            def on_task_finished(self):
                pass

            def _request_redraw(self, immediate=False):
                pass

            async def _consume(self, prompt, token, attachments=(), submitted=None):
                pass

            def redraw(self):
                pass

            def update_terminal_title(self, running=False):
                pass

            def _start_animation(self):
                pass

            def _append(self, kind: DisplayKind, val: str):
                test_self.rows.append((kind, str(val)))

            async def submit(self, text: str, **kwargs):
                test_self.submitted_prompts.append(text)
                test_self.submitted_skill = kwargs.get("skill_id")
                test_self.submitted_skills = kwargs.get("skill_ids")
                return True

            async def _handle_command(self, parsed):
                return True

        self.app = FakeApp()

    async def test_structured_skill_list_renders_categories_and_slash(self) -> None:
        handled = await handle_skill_command(self.app, "list")
        self.assertTrue(handled)
        output = self.rows[-1][1]
        self.assertIn("Installed Skills", output)
        self.assertIn("/academic-paper", output)
        self.assertIn("/cheat-seed", output)
        self.assertIn("/report-writing", output)
        self.assertIn("Direct slash: /<skill-name> [prompt]", output)

    async def test_skill_run_action_enables_and_submits(self) -> None:
        handled = await handle_skill_command(self.app, "run cheat-seed 帮我找个选题")
        self.assertTrue(handled)
        self.assertEqual(getattr(self, "submitted_skill", None), "cheat-seed")
        self.assertIn("帮我找个选题", self.submitted_prompts)

    async def test_skill_run_clears_pending_selection_when_submission_fails(self) -> None:
        self.app._pending_skill_id = "academic-paper"
        async def reject(_: str, **kwargs) -> bool:
            return False

        self.app.submit = reject
        handled = await handle_skill_command(self.app, "run cheat-seed 帮我找个选题")
        self.assertFalse(handled)
        self.assertIsNone(getattr(self.app, "_pending_skill_id", None))

    async def test_skill_run_without_prompt_is_used_by_next_submission(self) -> None:
        handled = await handle_skill_command(self.app, "run cheat-seed")
        self.assertTrue(handled)
        self.assertEqual(getattr(self.app, "_pending_skill_id", None), "cheat-seed")

    async def test_direct_slash_skill_without_prompt(self) -> None:
        accepted = await submit(self.app, "/academic-paper")
        self.assertTrue(accepted)
        self.assertEqual(getattr(self.app, "_pending_skill_id", None), "academic-paper")
        messages = [r[1] for r in self.rows]
        self.assertTrue(any("academic-paper" in m and "Ready" in m for m in messages))

    async def test_direct_slash_skill_with_prompt(self) -> None:
        # Mock start_prepared by providing a dummy controller
        class DummyController:
            async def ask(self, *args, **kwargs):
                if False:
                    yield None
        self.app.controller = DummyController()
        accepted = await submit(self.app, "/cheat-seed 帮我深挖一个观点视频选题")
        self.assertTrue(accepted)
        self.assertIsNone(getattr(self.app, "_pending_skill_id", None))
        # Check that user message was added and metadata acknowledged skill
        user_messages = [r[1] for r in self.rows if r[0] == DisplayKind.USER]
        self.assertIn("帮我深挖一个观点视频选题", user_messages)
        meta_messages = [r[1] for r in self.rows if r[0] == DisplayKind.METADATA]
        self.assertTrue(any("cheat-seed" in m and "active" in m for m in meta_messages))

    async def test_composed_direct_skill_preserves_declared_order(self) -> None:
        accepted = await submit(
            self.app,
            "/skill academic-paper + report-writing 整理成实验报告",
        )
        self.assertTrue(accepted)
        if self.app._run_task:
            await self.app._run_task
        self.assertEqual(self.enabled_skills[-2:], ["academic-paper", "report-writing"])

    async def test_unknown_slash_command_still_rejected(self) -> None:
        accepted = await submit(self.app, "/non-existent-cmd-xyz")
        self.assertFalse(accepted)
        errors = [r[1] for r in self.rows if r[0] == DisplayKind.ERROR]
        self.assertTrue(len(errors) > 0)

    async def test_skill_active_and_explain_are_bounded_metadata(self) -> None:
        await handle_skill_command(self.app, "active")
        active_output = self.rows[-1][1]
        self.assertIn("No active Skills", active_output)
        self.assertNotIn("Write and revise academic papers", active_output)

        await handle_skill_command(self.app, "explain academic-paper")
        explanation = self.rows[-1][1]
        self.assertIn("Skill: academic-paper", explanation)
        self.assertIn("Description:", explanation)
        self.assertNotIn("instruction", explanation.lower())


if __name__ == "__main__":
    unittest.main()
