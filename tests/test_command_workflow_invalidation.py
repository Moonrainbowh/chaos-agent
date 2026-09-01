from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionResult  # noqa: E402
from code_agent.workflows.models import WorkflowNodeStatus  # noqa: E402
from code_agent.workflows.observations import (  # noqa: E402
    TaskCreatedObservation,
    VerificationObservation,
)
from code_agent.workflows.service import WorkflowService  # noqa: E402
from code_agent_win.foreground_tasks import IntegratedForegroundTaskController  # noqa: E402


class _MemoryWorkflowStore:
    def __init__(self) -> None:
        self.snapshot = None

    async def save_workflow_snapshot(self, snapshot: object) -> None:
        self.snapshot = snapshot

    async def load_workflow_for_task(self, task_id: str):
        if self.snapshot is None or self.snapshot.workflow.task_id != task_id:
            return None
        return self.snapshot


class CommandWorkflowInvalidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _MemoryWorkflowStore()
        self.workflows = WorkflowService(self.store)
        await self.workflows.observe(TaskCreatedObservation("task", "thread", "repair"))
        await self.workflows.observe(
            VerificationObservation("task", "verification:one", True, ("evidence",))
        )
        self.controller = object.__new__(IntegratedForegroundTaskController)
        self.controller._sessions = self.store
        self.controller.workflows = self.workflows

    async def test_attempted_commands_invalidate_completed_workflow_evidence(self) -> None:
        for name in ("run_command", "run_process_v1"):
            with self.subTest(name=name):
                await self._complete_verification()
                await self.controller._observe_action(
                    "task",
                    _action_event(
                        ActionResult(
                            "call",
                            name,
                            {"error": "command failed"},
                            is_error=True,
                            metadata={"execution_attempted": True, "returncode": 1},
                        )
                    ),
                )
                node = _verification_node(self.store.snapshot)
                self.assertEqual(node.status, WorkflowNodeStatus.VERIFYING)
                self.assertEqual(node.evidence_refs, ())

    async def test_unattempted_command_preserves_completed_workflow_evidence(self) -> None:
        await self.controller._observe_action(
            "task",
            _action_event(
                ActionResult(
                    "call",
                    "run_process_v1",
                    {"error": "approval required"},
                    is_error=True,
                )
            ),
        )
        node = _verification_node(self.store.snapshot)
        self.assertEqual(node.status, WorkflowNodeStatus.COMPLETED)
        self.assertEqual(node.evidence_refs, ("evidence",))

    async def _complete_verification(self) -> None:
        node = _verification_node(self.store.snapshot)
        if node.status is not WorkflowNodeStatus.COMPLETED:
            await self.workflows.observe(
                VerificationObservation("task", node.id, True, ("evidence",))
            )


def _action_event(result: ActionResult) -> object:
    return SimpleNamespace(payload={"result": result.to_dict()})


def _verification_node(snapshot: object):
    return next(node for node in snapshot.nodes if node.kind == "verification")


if __name__ == "__main__":
    unittest.main()
