from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.completion_contract import CompletionKind  # noqa: E402
from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.core.verification_state import VerifierOutcome  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.verification.task_service import LedgerTaskVerificationService  # noqa: E402


class CommandInvalidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        container = Path(self.temporary.name).resolve()
        self.root = container / "workspace"
        self.root.mkdir()
        (self.root / "note.py").write_text("print('ok')\n", encoding="utf-8")
        self.sessions = SQLiteSessionRepository(container / "state.sqlite3")
        thread_id = await self.sessions.create_thread()
        self.task = await self.sessions.create_task(
            thread_id,
            TaskContract(
                "verify commands",
                TaskAuthorization.local_workspace(str(self.root)),
            ),
        )
        self.service = LedgerTaskVerificationService(self.root, self.sessions)
        self.state = await self.service.prepare(self.task, TaskState.empty())

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_attempted_commands_advance_generation_and_expire_evidence(self) -> None:
        verified = await self.service.record_action(
            self.task,
            ActionRequest("verify", "run_verification", {"kind": "python_unittest"}),
            ActionResult(
                "verify",
                "run_verification",
                {"kind": "python_unittest", "returncode": 0},
            ),
            self.state,
        )
        before = await self.service.assess(self.task, verified)
        self.assertEqual(before.assessment.kind, CompletionKind.VERIFIED)
        self.assertEqual(before.outcome, VerifierOutcome.PASS)

        for name, arguments in (
            ("run_command", {"command": "exit 1"}),
            ("run_process_v1", {"program": "python", "args": ["-c", "pass"]}),
        ):
            with self.subTest(name=name):
                advanced = await self.service.record_action(
                    self.task,
                    ActionRequest(f"{name}-call", name, arguments),
                    ActionResult(
                        f"{name}-call",
                        name,
                        {"error": "process start failed"},
                        is_error=True,
                        metadata={"execution_attempted": True},
                    ),
                    verified,
                )
                self.assertEqual(advanced.code_generation, verified.code_generation + 1)
                self.assertNotEqual(advanced.subject_hash, verified.subject_hash)
                after = await self.service.assess(self.task, advanced)
                self.assertEqual(after.assessment.kind, CompletionKind.UNVERIFIED)
                self.assertEqual(after.outcome, VerifierOutcome.NOT_RUN)

    async def test_unattempted_command_does_not_advance_generation(self) -> None:
        unchanged = await self.service.record_action(
            self.task,
            ActionRequest(
                "blocked",
                "run_process_v1",
                {"program": "cmd.exe", "args": ["/c", "echo"]},
            ),
            ActionResult(
                "blocked",
                "run_process_v1",
                {"error": "structured process does not accept shell launchers"},
                is_error=True,
            ),
            self.state,
        )
        self.assertIs(unchanged, self.state)


if __name__ == "__main__":
    unittest.main()
