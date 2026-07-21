from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from code_agent.workflows.models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
    WorkflowStatus,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class WorkflowModelTests(unittest.TestCase):
    def test_models_are_frozen_and_refs_are_tuple_backed(self) -> None:
        workflow = Workflow("wf-1", "thread-1", "task-1", "Repair", created_at=NOW)
        node = WorkflowNode(
            "node-1",
            workflow.id,
            "task",
            "Analyze",
            WorkflowNodeStatus.RUNNING,
            "thread-1",
            "main",
            ["input-1"],
            created_at=NOW,
            started_at=NOW,
        )
        edge = WorkflowEdge(
            workflow.id, node.id, "node-2", WorkflowEdgeKind.REQUIRES
        )
        snapshot = WorkflowSnapshot(workflow, [node], [edge])

        self.assertEqual(node.input_refs, ("input-1",))
        self.assertEqual(snapshot.nodes, (node,))
        with self.assertRaises(FrozenInstanceError):
            node.title = "changed"  # type: ignore[misc]

    def test_invalid_time_order_and_terminal_shape_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            WorkflowNode(
                "node-1",
                "wf-1",
                "task",
                "Broken",
                WorkflowNodeStatus.COMPLETED,
                None,
                "main",
                created_at=NOW,
                completed_at=NOW - timedelta(seconds=1),
            )
        with self.assertRaises(ValueError):
            WorkflowNode(
                "node-1",
                "wf-1",
                "task",
                "Broken",
                WorkflowNodeStatus.RUNNING,
                None,
                "main",
                created_at=NOW,
                completed_at=NOW,
            )

    def test_status_and_edge_values_are_stable(self) -> None:
        self.assertEqual(WorkflowStatus.ACTIVE.value, "active")
        self.assertEqual(WorkflowNodeStatus.WAITING_DECISION.value, "waiting_decision")
        self.assertEqual(WorkflowEdgeKind.REVIEW_OF.value, "review_of")


if __name__ == "__main__":
    unittest.main()
