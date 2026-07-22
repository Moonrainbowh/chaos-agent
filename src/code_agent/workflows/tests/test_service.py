from __future__ import annotations

import unittest

from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.service import (
    ChildRunObservation,
    DeliveryObservation,
    EvidenceInvalidatedObservation,
    RecoveryObservation,
    TaskCreatedObservation,
    VerificationObservation,
    WorkflowService,
)


class MemoryWorkflowStore:
    def __init__(self) -> None:
        self.by_task: dict[str, object] = {}
        self.fail = False

    async def save_workflow_snapshot(self, snapshot: object) -> None:
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.by_task[snapshot.workflow.task_id] = snapshot

    async def load_workflow_for_task(self, task_id: str) -> object | None:
        return self.by_task.get(task_id)


class WorkflowServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_requeues_stale_running_nodes_idempotently(self) -> None:
        service = WorkflowService(MemoryWorkflowStore())
        await service.observe(
            TaskCreatedObservation("task-1", "thread-1", "Repair")
        )

        first = await service.observe(RecoveryObservation("task-1"))
        second = await service.observe(RecoveryObservation("task-1"))

        main = next(node for node in first.nodes if node.kind == "main")
        self.assertEqual(main.status, WorkflowNodeStatus.QUEUED)
        self.assertEqual(second, first)

    async def test_task_child_verification_and_delivery_project_real_states(self) -> None:
        store = MemoryWorkflowStore()
        service = WorkflowService(store)

        await service.observe(
            TaskCreatedObservation("task-1", "thread-1", "Repair login")
        )
        await service.observe(
            ChildRunObservation(
                "task-1", "run-1", "child-1", "search", "Inspect calls",
                WorkflowNodeStatus.QUEUED,
            )
        )
        await service.observe(
            ChildRunObservation(
                "task-1", "run-1", "child-1", "search", "Inspect calls",
                WorkflowNodeStatus.COMPLETED,
            )
        )
        await service.observe(
            VerificationObservation("task-1", "verify-1", True, ("evidence-1",))
        )
        snapshot = await service.observe(
            DeliveryObservation("task-1", "delivery-1", WorkflowNodeStatus.COMPLETED)
        )

        statuses = {node.kind: node.status for node in snapshot.nodes}
        self.assertEqual(statuses["subagent"], WorkflowNodeStatus.COMPLETED)
        self.assertEqual(statuses["verification"], WorkflowNodeStatus.COMPLETED)
        self.assertEqual(statuses["delivery"], WorkflowNodeStatus.COMPLETED)
        verification = next(
            node for node in snapshot.nodes if node.kind == "verification"
        )
        self.assertEqual(verification.evidence_refs, ("evidence-1",))

    async def test_invalidated_evidence_returns_verification_to_verifying(self) -> None:
        service = WorkflowService(MemoryWorkflowStore())
        await service.observe(
            TaskCreatedObservation("task-1", "thread-1", "Repair")
        )
        await service.observe(
            VerificationObservation("task-1", "verify-1", True, ("evidence-1",))
        )

        snapshot = await service.observe(
            EvidenceInvalidatedObservation("task-1", "verify-1")
        )

        node = next(item for item in snapshot.nodes if item.id == "verify-1")
        self.assertEqual(node.status, WorkflowNodeStatus.VERIFYING)
        self.assertEqual(node.evidence_refs, ())

    async def test_model_dict_is_rejected_and_failed_save_is_not_published(self) -> None:
        store = MemoryWorkflowStore()
        service = WorkflowService(store)
        published: list[object] = []
        service.subscribe(published.append)
        with self.assertRaises(TypeError):
            await service.observe({"kind": "task_created"})  # type: ignore[arg-type]
        store.fail = True
        with self.assertRaises(RuntimeError):
            await service.observe(
                TaskCreatedObservation("task-1", "thread-1", "Repair")
            )
        self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()
