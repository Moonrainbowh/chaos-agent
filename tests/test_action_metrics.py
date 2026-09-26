from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent_win.action_metrics import ActionMetricsCollector  # noqa: E402


class ActionMetricsTests(unittest.TestCase):
    def test_snapshot_aggregates_task_cost_without_retaining_path_text(self) -> None:
        collector = ActionMetricsCollector()
        collector.record(
            "task-1",
            ActionRequest("read-1", "read_file", {"path": "src/a.py"}),
            ActionResult("read-1", "read_file", {}, metadata={"duration_ms": 10}),
        )
        collector.record(
            "task-1",
            ActionRequest("read-2", "read_file", {"path": "src/a.py"}),
            ActionResult("read-2", "read_file", {}, metadata={"duration_ms": 20}),
        )
        collector.record(
            "task-1",
            ActionRequest("write-1", "write_file", {"path": "src/a.py"}),
            ActionResult("write-1", "write_file", {}, True, {"duration_ms": 5}),
        )
        collector.record(
            "task-1",
            ActionRequest("write-2", "write_file", {"path": "src/a.py"}),
            ActionResult("write-2", "write_file", {}, metadata={"duration_ms": 5}),
        )
        collector.record(
            "task-1",
            ActionRequest("verify-1", "run_verification", {"kind": "compile"}),
            ActionResult("verify-1", "run_verification", {}, metadata={"duration_ms": 7}),
        )

        result = collector.snapshot("task-1")

        self.assertEqual(result.actions, 5)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.total_duration_ms, 47)
        self.assertEqual(result.repeated_reads, 1)
        self.assertEqual(result.retries, 1)
        self.assertEqual(result.by_name["read_file"], 2)
        self.assertEqual(result.category_duration_ms["workspace_read"], 30)
        self.assertEqual(result.category_duration_ms["workspace_edit"], 10)
        self.assertGreater(result.p95_duration_ms, result.p50_duration_ms)
        self.assertNotIn("src/a.py", repr(collector.__dict__))

    def test_tasks_are_isolated_and_missing_task_is_empty(self) -> None:
        collector = ActionMetricsCollector()
        collector.record(
            "task-1",
            ActionRequest("call", "git_status", {}),
            ActionResult("call", "git_status", {}, metadata={"duration_ms": 4}),
        )

        self.assertEqual(collector.snapshot("task-2").actions, 0)
        self.assertEqual(
            collector.snapshot("task-1").category_duration_ms,
            {"git": 4},
        )

    def test_read_variants_are_deduplicated_and_signatures_include_arguments(self) -> None:
        collector = ActionMetricsCollector()
        for request in (
            ActionRequest("slice-1", "read_code_slices", {"targets": ["a.py"]}),
            ActionRequest("slice-2", "read_code_slices", {"targets": ["a.py"]}),
            ActionRequest("slice-3", "read_code_slices", {"targets": ["b.py"]}),
        ):
            collector.record(
                "task-1",
                request,
                ActionResult(request.id, request.name, {}, metadata={"duration_ms": 0.6}),
            )

        result = collector.snapshot("task-1")
        self.assertEqual(result.repeated_reads, 1)
        self.assertEqual(result.category_duration_ms["workspace_read"], 3)
        self.assertEqual(result.retries, 0)

    def test_reads_after_a_successful_edit_start_a_new_generation(self) -> None:
        collector = ActionMetricsCollector()
        collector.record(
            "task-1",
            ActionRequest("read-1", "read_file", {"path": "a.py"}),
            ActionResult("read-1", "read_file", {}, metadata={"duration_ms": 1}),
        )
        collector.record(
            "task-1",
            ActionRequest("edit", "write_file", {"path": "a.py", "content": "x"}),
            ActionResult("edit", "write_file", {}, metadata={"duration_ms": 1}),
        )
        collector.record(
            "task-1",
            ActionRequest("read-2", "read_file", {"path": "a.py"}),
            ActionResult("read-2", "read_file", {}, metadata={"duration_ms": 1}),
        )

        self.assertEqual(collector.snapshot("task-1").repeated_reads, 0)

    def test_rolled_back_edit_does_not_start_a_new_generation(self) -> None:
        collector = ActionMetricsCollector()
        collector.record(
            "task-1",
            ActionRequest("read-1", "read_file", {"path": "a.py"}),
            ActionResult("read-1", "read_file", {}, metadata={"duration_ms": 1}),
        )
        collector.record(
            "task-1",
            ActionRequest("rollback", "apply_workspace_edit_plan_v1", {}),
            ActionResult(
                "rollback",
                "apply_workspace_edit_plan_v1",
                {"workspace_may_have_changed": False},
                True,
                {"duration_ms": 1},
            ),
        )
        collector.record(
            "task-1",
            ActionRequest("read-2", "read_file", {"path": "a.py"}),
            ActionResult("read-2", "read_file", {}, metadata={"duration_ms": 1}),
        )

        self.assertEqual(collector.snapshot("task-1").repeated_reads, 1)


if __name__ == "__main__":
    unittest.main()
