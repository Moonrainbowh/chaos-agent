from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.core.completion_contract import TaskIntent
from code_agent.core.task_state import TaskState
from code_agent.verification.task_service import LedgerTaskVerificationService


class CompletionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_modify_without_changed_files_does_not_schedule_project_tests(self):
        service = LedgerTaskVerificationService(Path.cwd(), sessions=object())
        task = SimpleNamespace(contract=SimpleNamespace(intent=TaskIntent.MODIFY))

        suggested = await service.suggest_verification(task, TaskState())

        self.assertIsNone(suggested)


if __name__ == "__main__":
    unittest.main()
