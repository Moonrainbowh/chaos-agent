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

    def test_default_authorization_never_enables_network_or_outside_workspace(self) -> None:
        authorization = TaskAuthorization.local_workspace("C:/repo")
        self.assertTrue(authorization.allow_workspace_write)
        self.assertTrue(authorization.allow_local_execute)
        self.assertFalse(authorization.allow_network)
        self.assertFalse(authorization.allow_outside_workspace)
