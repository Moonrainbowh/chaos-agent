from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.core.models import ToolDefinition
from code_agent.skills.controller import SkillController
from code_agent.skills.capabilities import SkillCapabilityError


class Approval:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[str] = []

    async def approve(self, identifier: str, source: str, digest: str) -> bool:
        self.calls.append(identifier)
        return self.accepted


class SkillControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_capability_blocks_activation_before_approval_or_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".agents" / "skills" / "review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: review\nrequires:\n  - read_workspace\n---\nReview.",
                encoding="utf-8",
            )
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            approval = Approval()
            controller = SkillController(
                root,
                sessions,
                approval,
                capability_snapshot=lambda: (),
            )

            with self.assertRaises(SkillCapabilityError) as raised:
                await controller.enable(thread_id, "review")

            self.assertEqual(raised.exception.checks[0].missing, ("read_workspace",))
            self.assertEqual(approval.calls, [])
            self.assertEqual(await sessions.list_skill_activations(thread_id), ())

    async def test_combined_activation_checks_all_skills_before_enabling_any(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for identifier, requirement in (("review", "read_workspace"), ("report", "report_exporter")):
                skill_dir = root / ".agents" / "skills" / identifier
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {identifier}\nrequires: [{requirement}]\n---\nUse it.",
                    encoding="utf-8",
                )
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            controller = SkillController(
                root,
                sessions,
                Approval(),
                capability_snapshot=lambda: (
                    ToolDefinition("read_workspace", "read", {}),
                ),
            )

            with self.assertRaises(SkillCapabilityError):
                await controller.enable_many(thread_id, ("review", "report"))

            self.assertEqual(await sessions.list_skill_activations(thread_id), ())

    async def test_enable_restore_disable_and_digest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".agents" / "skills" / "review"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("Review carefully.", encoding="utf-8")
            database = root / "sessions.sqlite3"
            sessions = SQLiteSessionRepository(database)
            thread_id = await sessions.create_thread()
            approval = Approval()
            controller = SkillController(root, sessions, approval)

            enabled = await controller.enable(thread_id, "review")

            self.assertEqual(enabled.identifier, "review")
            self.assertEqual(approval.calls, ["review"])
            restored = SkillController(root, sessions, approval)
            self.assertEqual(
                [item.identifier for item in await restored.restore(thread_id)],
                ["review"],
            )
            self.assertNotIn(b"Review carefully.", database.read_bytes())
            skill_file.write_text("Changed instructions.", encoding="utf-8")
            drifted = SkillController(root, sessions, approval)
            self.assertEqual(await drifted.restore(thread_id), ())
            self.assertEqual(await sessions.list_skill_activations(thread_id), ())

    async def test_rejected_workspace_skill_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".agents" / "skills" / "review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("Review.", encoding="utf-8")
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            controller = SkillController(root, sessions, Approval(False))

            with self.assertRaises(PermissionError):
                await controller.enable(thread_id, "review")

            self.assertEqual(await sessions.list_skill_activations(thread_id), ())


if __name__ == "__main__":
    unittest.main()
