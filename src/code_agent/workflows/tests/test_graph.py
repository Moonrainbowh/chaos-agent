from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.workflows.graph import (
    WorkflowCycleError,
    WorkflowGraph,
    WorkflowTransitionError,
)
from code_agent.workflows.models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _node(identifier: str, workflow_id: str = "wf-1") -> WorkflowNode:
    return WorkflowNode(
        identifier,
        workflow_id,
        "task",
        identifier,
        WorkflowNodeStatus.PLANNED,
        created_at=NOW,
    )


class WorkflowGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = WorkflowGraph(
            Workflow("wf-1", "thread-1", "task-1", "Repair", created_at=NOW)
        )

    def test_sequence_parallel_branches_and_join_are_accepted(self) -> None:
        for identifier in ("root", "search", "review", "join"):
            self.graph.add_node(_node(identifier))
        for source, target in (
            ("root", "search"),
            ("root", "review"),
            ("search", "join"),
            ("review", "join"),
        ):
            self.graph.add_edge(
                WorkflowEdge("wf-1", source, target, WorkflowEdgeKind.REQUIRES)
            )

        snapshot = self.graph.snapshot()

        self.assertEqual(len(snapshot.nodes), 4)
        self.assertEqual(len(snapshot.edges), 4)

    def test_self_cross_workflow_and_multinode_cycles_are_rejected(self) -> None:
        self.graph.add_node(_node("a"))
        self.graph.add_node(_node("b"))
        with self.assertRaises(WorkflowCycleError):
            self.graph.add_edge(
                WorkflowEdge("wf-1", "a", "a", WorkflowEdgeKind.REQUIRES)
            )
        with self.assertRaises(ValueError):
            self.graph.add_edge(
                WorkflowEdge("wf-2", "a", "b", WorkflowEdgeKind.REQUIRES)
            )
        self.graph.add_edge(
            WorkflowEdge("wf-1", "a", "b", WorkflowEdgeKind.REQUIRES)
        )
        with self.assertRaises(WorkflowCycleError):
            self.graph.add_edge(
                WorkflowEdge("wf-1", "b", "a", WorkflowEdgeKind.REQUIRES)
            )

    def test_legal_transitions_set_times_and_terminal_cannot_restart(self) -> None:
        self.graph.add_node(_node("a"))

        self.graph.transition("a", WorkflowNodeStatus.QUEUED, at=NOW)
        running = self.graph.transition("a", WorkflowNodeStatus.RUNNING, at=NOW)
        completed = self.graph.transition(
            "a", WorkflowNodeStatus.COMPLETED, at=NOW
        )

        self.assertEqual(running.started_at, NOW)
        self.assertEqual(completed.completed_at, NOW)
        with self.assertRaises(WorkflowTransitionError):
            self.graph.transition("a", WorkflowNodeStatus.RUNNING, at=NOW)

    def test_illegal_transition_fails_without_mutating_snapshot(self) -> None:
        self.graph.add_node(_node("a"))
        before = self.graph.snapshot()

        with self.assertRaises(WorkflowTransitionError):
            self.graph.transition("a", WorkflowNodeStatus.COMPLETED, at=NOW)

        self.assertEqual(self.graph.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
