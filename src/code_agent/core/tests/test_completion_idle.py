from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.core.completion_contract import CompletionAssessment, CompletionKind, TaskIntent
from code_agent.core.engine_completion import AgentEngineCompletionMixin
from code_agent.core.events import EventKind
from code_agent.core.task import TaskStatus
from code_agent.core.task_intent import infer_task_intent, is_small_talk


class CompletionIdleTests(unittest.IsolatedAsyncioTestCase):
    def test_greeting_variants_are_analysis_but_work_requests_remain_modifications(self):
        for text in ("你好", "你好呀嘻嘻嘻", " Hello! ", "谢谢呀", "hi"):
            self.assertTrue(is_small_talk(text))
            self.assertEqual(infer_task_intent(text, "code"), TaskIntent.ANALYZE)
        for text in ("你好，帮我修改代码", "hello fix tests", "谢谢，继续执行", "write a hello function"):
            self.assertFalse(is_small_talk(text))
            self.assertEqual(infer_task_intent(text, "code"), TaskIntent.MODIFY)

    async def test_no_verifier_leaves_a_durable_waiting_reason_not_permanent_verifying(self):
        class Journal:
            async def transition_task(self, identifier, status, reason):
                return SimpleNamespace(id=identifier, status=status, stop_reason=reason)

        engine = AgentEngineCompletionMixin()
        engine._journal = Journal()
        engine._verification = None
        task = SimpleNamespace(id="task", contract=SimpleNamespace(intent=TaskIntent.MODIFY), status=TaskStatus.RUNNING)
        result = await engine._resolve_task_completion(task, "thread")
        self.assertEqual(result.status, TaskStatus.WAITING_DECISION)
        self.assertIn("Missing", result.stop_reason)
        events = engine._task_completion_events("thread", result, None, None)
        self.assertEqual([item.kind for item in events], [EventKind.TASK_STATUS_CHANGED, EventKind.TASK_DECISION_REQUIRED])
        self.assertEqual(events[-1].payload["reason"], result.stop_reason)
