from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.interfaces.workflow_view import WorkflowView
from code_agent.workflows.models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _snapshot() -> WorkflowSnapshot:
    workflow = Workflow("wf-1", "thread-1", "task-1", "Repair", created_at=NOW)
    nodes = (
        WorkflowNode(
            "main", "wf-1", "main", "Analyze", WorkflowNodeStatus.RUNNING,
            role="main", created_at=NOW, started_at=NOW,
        ),
        WorkflowNode(
            "search", "wf-1", "subagent", "Search calls", WorkflowNodeStatus.COMPLETED,
            role="search", created_at=NOW, started_at=NOW, completed_at=NOW,
        ),
        WorkflowNode(
            "review", "wf-1", "subagent", "Review\x1b[31m", WorkflowNodeStatus.FAILED,
            role="review", created_at=NOW, started_at=NOW, completed_at=NOW,
        ),
    )
    edges = (
        WorkflowEdge("wf-1", "main", "search", WorkflowEdgeKind.REQUIRES),
        WorkflowEdge("wf-1", "main", "review", WorkflowEdgeKind.REQUIRES),
    )
    return WorkflowSnapshot(workflow, nodes, edges)


class WorkflowViewTests(unittest.TestCase):
    def test_renders_branch_snapshot_and_sanitizes_titles(self) -> None:
        rendered = WorkflowView().render(_snapshot(), width=80)

        self.assertIn("Task: Repair", rendered)
        self.assertIn("├─✓", rendered)
        self.assertIn("└─×", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_failure_filter_and_narrow_width_are_bounded(self) -> None:
        rendered = WorkflowView().render(_snapshot(), width=24, filter_name="失败")

        self.assertIn("Review", rendered)
        self.assertNotIn("Search calls", rendered)
        self.assertTrue(all(len(line) <= 24 for line in rendered.splitlines()))

    def test_detail_and_evidence_are_read_only(self) -> None:
        snapshot = _snapshot()
        detail = WorkflowView().detail(snapshot, "review", width=80)
        evidence = WorkflowView().evidence(snapshot, "review", width=80)

        self.assertIn("Node review", detail)
        self.assertIn("Status: failed", detail)
        self.assertIn("evidence: none", evidence)


if __name__ == "__main__":
    unittest.main()
