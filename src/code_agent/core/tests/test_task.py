from __future__ import annotations

import unittest

from code_agent.core.task import TaskAuthorization, TaskRecord, TaskStatus


class TaskTests(unittest.TestCase):
    def test_running_task_can_pause_and_resume(self) -> None:
        task = TaskRecord.new("thread-1", "repair provider tests", TaskAuthorization.local_workspace("C:/repo"))
        paused = task.transition(TaskStatus.RUNNING).transition(TaskStatus.PAUSED, "user requested pause")
        self.assertEqual(paused.transition(TaskStatus.RUNNING).status, TaskStatus.RUNNING)

    def test_completed_task_cannot_resume(self) -> None:
        task = TaskRecord.new("thread-1", "inspect", TaskAuthorization.local_workspace("C:/repo"))
        completed = task.transition(TaskStatus.RUNNING).transition(TaskStatus.COMPLETED)
        with self.assertRaises(ValueError):
            completed.transition(TaskStatus.RUNNING)

    def test_active_and_quiescent_tasks_can_be_superseded_terminally(self) -> None:
        initial = TaskRecord.new(
            "thread-1", "inspect", TaskAuthorization.local_workspace("C:/repo")
        )
        states = (
            initial.transition(TaskStatus.RUNNING),
            initial.transition(TaskStatus.RUNNING).transition(TaskStatus.VERIFYING),
            initial.transition(TaskStatus.PAUSED),
            initial.transition(TaskStatus.INTERRUPTED),
            initial.transition(TaskStatus.RUNNING).transition(TaskStatus.WAITING_DECISION),
        )
        for task in states:
            with self.subTest(status=task.status):
                superseded = task.transition(TaskStatus.SUPERSEDED, "rewound")
                self.assertEqual(superseded.status, TaskStatus.SUPERSEDED)
                with self.assertRaises(ValueError):
                    superseded.transition(TaskStatus.RUNNING)

        with self.assertRaises(ValueError):
            initial.transition(TaskStatus.SUPERSEDED)

    def test_existing_terminal_tasks_cannot_be_superseded(self) -> None:
        running = TaskRecord.new(
            "thread-1", "inspect", TaskAuthorization.local_workspace("C:/repo")
        ).transition(TaskStatus.RUNNING)
        terminal = (
            running.transition(TaskStatus.COMPLETED),
            running.transition(TaskStatus.FAILED),
            running.transition(TaskStatus.VERIFYING).transition(
                TaskStatus.ACCEPTED_PARTIAL
            ),
        )
        for task in terminal:
            with self.subTest(status=task.status):
                with self.assertRaises(ValueError):
                    task.transition(TaskStatus.SUPERSEDED)

    def test_partial_acceptance_is_terminal_and_requires_verification_or_decision(self) -> None:
        task = TaskRecord.new("thread-1", "inspect", TaskAuthorization.local_workspace("C:/repo"))
        accepted = task.transition(TaskStatus.RUNNING).transition(TaskStatus.VERIFYING).transition(TaskStatus.ACCEPTED_PARTIAL)
        self.assertEqual(accepted.status, TaskStatus.ACCEPTED_PARTIAL)
        with self.assertRaises(ValueError):
            accepted.transition(TaskStatus.RUNNING)
        with self.assertRaises(ValueError):
            task.transition(TaskStatus.ACCEPTED_PARTIAL)

    def test_default_authorization_never_enables_network_or_outside_workspace(self) -> None:
        authorization = TaskAuthorization.local_workspace("C:/repo")
        self.assertTrue(authorization.allow_workspace_write)
        self.assertTrue(authorization.allow_local_execute)
        self.assertFalse(authorization.allow_network)
        self.assertFalse(authorization.allow_outside_workspace)
