"""Independent regressions for pre-review user input and budget pauses."""
import unittest
import json
from unittest.mock import patch
from tests import test_parent_source_review as support
from code_agent.core.models import Message
from code_agent.core.task_supervisor import TaskSupervisor, SupervisionDecision, SupervisionKind
from code_agent.core.errors import EngineLimitError

answer = support.answer

class ParentReviewBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.ParentSourceReviewTests.asyncSetUp
    asyncTearDown = support.ParentSourceReviewTests.asyncTearDown
    provider = support.ParentSourceReviewTests.provider
    run_parent = support.ParentSourceReviewTests.run_parent
    source_run = support.ParentSourceReviewTests.source_run
    delivery = support.ParentSourceReviewTests.delivery

    async def test_followup_before_first_prepare(self):
        host = self.app.controller._engine._parent_review
        original = host.prepare
        injected = False
        marker = 'NEW_USER_REQUIREMENT_BEFORE_FIRST_REVIEW'

        async def prepare(task, cancellation):
            nonlocal injected
            if task is not None and not injected:
                messages = await self.app.sessions.load_messages(task.thread_id)
                if any(m.role == 'tool' and m.name == 'delegate_agent' for m in messages):
                    injected = True
                    await self.app.sessions.append_message(task.thread_id, Message('user', marker))
            return await original(task, cancellation)

        host.prepare = prepare
        _, _, parent, _, _ = await self.source_run([[answer('invalid initial')], [answer('invalid repair')]])
        self.assertTrue(injected)
        self.assertIn(marker, json.dumps(parent.bodies[2]))
        self.assertNotIn('Child full advisory.', json.dumps(parent.bodies[2]))

    async def test_supervisor_pause_before_review_prepare(self):
        original = TaskSupervisor.before_model_turn
        parent_calls = 0

        def decide(supervisor):
            nonlocal parent_calls
            if supervisor._contract.objective.startswith('Read only: deep source investigation.'):
                parent_calls += 1
                if parent_calls == 3:
                    return SupervisionDecision(SupervisionKind.PAUSE, 'active time budget exceeded')
            return original(supervisor)

        with patch.object(TaskSupervisor, 'before_model_turn', decide):
            task, events, parent, _, _ = await self.source_run([])
        self.assertEqual(task.status.value, 'paused')
        self.assertEqual(len(parent.bodies), 2)
        budget = await self.app.sessions.load_task_budget(task.id)
        self.assertEqual(budget.tool_calls, 3)
        paused = [e for e in events if e.kind.value == 'task_paused'][-1]
        self.assertTrue(paused.payload.get('result', {}).get('remaining'), paused.to_dict())
        self.assertEqual(paused.payload['result']['verification_status'], 'unverified')

    async def test_hard_failure_before_review_prepare(self):
        original = TaskSupervisor.before_model_turn
        parent_calls = 0

        def decide(supervisor):
            nonlocal parent_calls
            if supervisor._contract.objective.startswith('Read only: deep source investigation.'):
                parent_calls += 1
                if parent_calls == 3:
                    raise EngineLimitError('model turn budget exceeded')
            return original(supervisor)

        with patch.object(TaskSupervisor, 'before_model_turn', decide):
            task, events, parent, _, _ = await self.source_run([])
        self.assertEqual(task.status.value, 'paused')
        self.assertEqual(len(parent.bodies), 2)
        budget = await self.app.sessions.load_task_budget(task.id)
        self.assertEqual(budget.tool_calls, 3)
        paused = [e for e in events if e.kind.value == 'task_paused'][-1]
        self.assertTrue(paused.payload.get('result', {}).get('remaining'), paused.to_dict())
        self.assertEqual(paused.payload['result']['verification_status'], 'unverified')
