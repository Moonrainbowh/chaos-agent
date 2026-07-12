from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.task_supervisor import SupervisionKind, TaskSupervisor


class TaskSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = TaskContract("repair", TaskAuthorization.local_workspace("C:/repo"))

    def test_same_validation_failure_three_times_pauses_task(self) -> None:
        supervisor = TaskSupervisor(self.contract)
        for _ in range(2):
            self.assertEqual(supervisor.observe_validation("pytest:1:abc", 1).kind, SupervisionKind.CONTINUE)
        decision = supervisor.observe_validation("pytest:1:abc", 1)
        self.assertEqual(decision.kind, SupervisionKind.PAUSE)
        self.assertEqual(decision.reason, "repeated validation failure")

    def test_new_failure_signature_resets_repetition_counter(self) -> None:
        supervisor = TaskSupervisor(self.contract)
        supervisor.observe_validation("pytest:1:abc", 1)
        self.assertEqual(supervisor.observe_validation("pytest:1:def", 1).kind, SupervisionKind.CONTINUE)

    def test_expired_task_stops_before_next_model_turn(self) -> None:
        contract = replace(self.contract, max_active_seconds=1)
        past = datetime.now(timezone.utc) - timedelta(seconds=2)
        self.assertEqual(TaskSupervisor(contract, started_at=past).before_model_turn().kind, SupervisionKind.PAUSE)
