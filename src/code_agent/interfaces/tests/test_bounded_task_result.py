"""The production result consumer uses the bounded identity projection."""
import unittest
from pathlib import Path

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskAuthorization, TaskRecord, TaskStatus
from code_agent.core.task_result import TaskResult
from code_agent.core.task_state import TaskState
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController


class BoundedTaskResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_never_loads_complete_event_history(self):
        task = TaskRecord.new("thread", "Inspect sources", TaskAuthorization("workspace"))
        task = task.transition(TaskStatus.RUNNING).transition(TaskStatus.WAITING_DECISION)
        state = TaskState.empty()
        recorded = TaskResult("waiting_decision", "unchanged", "unverified",
                              ("Need user decision",), "no_change")
        class Sessions:
            async def load_task(self, task_id):
                return task
            async def load_task_state(self, thread_id):
                return state
            async def load_events(self, thread_id):
                raise AssertionError("unbounded history read")
            async def load_latest_task_events(self, task_id, generation, subject_hash):
                self.identity = (task_id, generation, subject_hash)
                return (AgentEvent(EventKind.TASK_RESULT, {
                    "task_id": task.id, "result_generation": state.code_generation,
                    "result_subject_hash": state.subject_hash,
                    "task_updated_at": task.updated_at.isoformat(),
                    "result": recorded.to_dict()}),)
        sessions = Sessions()
        class Runner:
            async def run(self, *args, **kwargs):
                if False:
                    yield None
        controller = ForegroundTaskController(AgentController(Runner()), sessions, Path("workspace"))
        self.assertEqual(await controller.result(task.id), recorded)
        self.assertEqual(sessions.identity, (task.id, state.code_generation, state.subject_hash))


if __name__ == "__main__":
    unittest.main()
