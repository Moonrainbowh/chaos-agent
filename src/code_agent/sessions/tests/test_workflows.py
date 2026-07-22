from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workflows.graph import WorkflowGraph
from code_agent.workflows.models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
)


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class WorkflowRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_workflow_snapshot_round_trips_after_restart(self) -> None:
        thread_id = await self.repository.create_thread()
        workflow = Workflow(
            "wf-1", thread_id, "task-1", "Repair login", created_at=NOW
        )
        graph = WorkflowGraph(workflow)
        for identifier in ("main", "search", "join"):
            graph.add_node(
                WorkflowNode(
                    identifier,
                    workflow.id,
                    "task",
                    identifier,
                    WorkflowNodeStatus.PLANNED,
                    assigned_thread_id=thread_id,
                    created_at=NOW,
                )
            )
        graph.add_edge(
            WorkflowEdge(
                workflow.id, "main", "search", WorkflowEdgeKind.REQUIRES
            )
        )
        graph.add_edge(
            WorkflowEdge(
                workflow.id, "search", "join", WorkflowEdgeKind.PRODUCES
            )
        )

        await self.repository.save_workflow_snapshot(graph.snapshot())
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(
            await reopened.load_workflow_snapshot(workflow.id), graph.snapshot()
        )
        self.assertEqual(
            await reopened.load_workflow_for_task("task-1"), graph.snapshot()
        )

    async def test_node_status_update_replaces_one_trusted_snapshot(self) -> None:
        thread_id = await self.repository.create_thread()
        workflow = Workflow("wf-1", thread_id, "task-1", "Repair", created_at=NOW)
        graph = WorkflowGraph(workflow)
        graph.add_node(
            WorkflowNode(
                "main",
                workflow.id,
                "task",
                "Main",
                WorkflowNodeStatus.PLANNED,
                created_at=NOW,
            )
        )
        await self.repository.save_workflow_snapshot(graph.snapshot())
        graph.transition("main", WorkflowNodeStatus.QUEUED, at=NOW)

        await self.repository.save_workflow_snapshot(graph.snapshot())

        restored = await self.repository.load_workflow_snapshot(workflow.id)
        self.assertEqual(restored.nodes[0].status, WorkflowNodeStatus.QUEUED)


if __name__ == "__main__":
    unittest.main()
