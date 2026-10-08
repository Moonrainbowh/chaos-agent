import unittest
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task_result import TaskResult, ResultCollector


class TaskResultTests(unittest.TestCase):
    def test_exit_semantics_keep_execution_and_verification_separate(self):
        for status, code in [('completed', 0), ('failed', 1), ('cancelled', 130),
                             ('waiting_decision', 3), ('paused', 4), ('interrupted', 4),
                             ('unknown', 4), ('accepted_partial', 5)]:
            with self.subTest(status=status):
                self.assertEqual(TaskResult(status).exit_code(), code)
        self.assertEqual(TaskResult('completed').exit_code(require_verified=True), 5)
        self.assertEqual(TaskResult('completed', verification_status='verified').exit_code(require_verified=True), 0)

    def test_empty_or_text_only_stream_does_not_prove_success(self):
        collector = ResultCollector()
        collector.observe(AgentEvent(EventKind.MESSAGE_ADDED, {'message': {'role': 'assistant', 'content': 'done'}}))
        self.assertEqual(collector.result.execution_status, 'unknown')

    def test_cancel_cannot_be_overwritten_by_completion(self):
        collector = ResultCollector()
        collector.observe(AgentEvent(EventKind.CANCELLED, {'reason': 'stop'}))
        collector.observe(AgentEvent(EventKind.COMPLETED))
        self.assertEqual(collector.result.execution_status, 'cancelled')

    def test_recoverable_error_then_explicit_completion_is_not_permanent_failure(self):
        collector = ResultCollector()
        collector.observe(AgentEvent(EventKind.ERROR))
        self.assertEqual(collector.result.execution_status, 'failed')
        collector.observe(AgentEvent(EventKind.COMPLETED))
        self.assertEqual(collector.result.execution_status, 'completed')
        self.assertEqual(collector.result.verification_status, 'unknown')

    def test_result_roundtrip_and_future_version_rejection(self):
        result = TaskResult('waiting_decision', 'changed', 'unverified', ('tests',), 'verification_missing')
        self.assertEqual(TaskResult.from_dict(result.to_dict()), result)
        with self.assertRaises(ValueError):
            TaskResult.from_dict({'version': 2})

    def test_tool_result_does_not_replace_task_result(self):
        collector = ResultCollector()
        collector.observe(AgentEvent(EventKind.TASK_DECISION_REQUIRED))
        collector.observe(AgentEvent(EventKind.ACTION_COMPLETED, {'result': {'request_id': 'x', 'is_error': False}}))
        self.assertEqual(collector.result.execution_status, 'waiting_decision')

    def test_cancel_event_with_result_cannot_upgrade_to_complete(self):
        collector = ResultCollector()
        collector.observe(AgentEvent(EventKind.CANCELLED, {'result': TaskResult('completed').to_dict()}))
        collector.observe(AgentEvent(EventKind.COMPLETED))
        self.assertEqual(collector.result.execution_status, 'cancelled')
