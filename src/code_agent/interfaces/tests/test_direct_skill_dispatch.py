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
        }

        test_self = self

        class FakeSkillsController:
            def list(self):
                return tuple(skills_dict.values())

            def info(self, ident: str):
                if ident in skills_dict:
                    return skills_dict[ident]
                raise KeyError(ident)

            async def enable(self, thread_id: str, ident: str):
                test_self.enabled_skills.append(ident)

            def activation(self, thread_id: str):
                return SimpleNamespace(active=lambda: [skills_dict["academic-paper"]])

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

            async def submit(self, text: str):
                test_self.submitted_prompts.append(text)
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
        self.assertIn("●", output)
        self.assertIn("○", output)
        self.assertIn("Direct slash: /<skill-name> [prompt]", output)

    async def test_skill_run_action_enables_and_submits(self) -> None:
        handled = await handle_skill_command(self.app, "run cheat-seed 帮我找个选题")
        self.assertTrue(handled)
        self.assertIn("cheat-seed", self.enabled_skills)
        self.assertIn("帮我找个选题", self.submitted_prompts)

    async def test_direct_slash_skill_without_prompt(self) -> None:
        accepted = await submit(self.app, "/academic-paper")
        self.assertTrue(accepted)
        self.assertIn("academic-paper", self.enabled_skills)
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
        self.assertIn("cheat-seed", self.enabled_skills)
        # Check that user message was added and metadata acknowledged skill
        user_messages = [r[1] for r in self.rows if r[0] == DisplayKind.USER]
        self.assertIn("帮我深挖一个观点视频选题", user_messages)
        meta_messages = [r[1] for r in self.rows if r[0] == DisplayKind.METADATA]
        self.assertTrue(any("cheat-seed" in m and "active" in m for m in meta_messages))

    async def test_unknown_slash_command_still_rejected(self) -> None:
        accepted = await submit(self.app, "/non-existent-cmd-xyz")
        self.assertFalse(accepted)
        errors = [r[1] for r in self.rows if r[0] == DisplayKind.ERROR]
        self.assertTrue(len(errors) > 0)


if __name__ == "__main__":
    unittest.main()
