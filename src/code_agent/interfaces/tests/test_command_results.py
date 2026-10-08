import json
import unittest
from types import SimpleNamespace
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task_result import TaskResult
from code_agent.interfaces.commands import parse_command, execute_command


class Tasks:
    def __init__(self, events, result=None):
        self.items, self.current = events, result
    async def start(self, prompt):
        return SimpleNamespace(id='task')
    async def events(self, *args, **kwargs):
        for event in self.items:
            yield event
    async def result(self, task_id):
        if self.current is None:
            raise RuntimeError('state unavailable')
        return self.current


class CommandResultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_reports_real_status_and_exit(self):
        for status in ('completed', 'failed', 'cancelled', 'waiting_decision', 'interrupted'):
            with self.subTest(status=status):
                expected = TaskResult(status, 'changed', 'unverified')
                output = []
                code = await execute_command(parse_command(('run', '--json', 'task')),
                    None, None, output.append, Tasks((), expected))
                final = json.loads(output[-1])
                self.assertEqual(final['kind'], 'task_result')
                self.assertEqual(final['payload']['result']['execution_status'], status)
                self.assertEqual(code, expected.exit_code())

    async def test_strict_verification_and_read_failure_fail_closed(self):
        for verified, expected in [('unverified', 5), ('verified', 0)]:
            code = await execute_command(parse_command(('run', '--json', '--require-verified', 'task')),
                None, None, lambda x: None, Tasks((), TaskResult('completed', verification_status=verified)))
            self.assertEqual(code, expected)
        output = []
        code = await execute_command(parse_command(('run', '--json', 'task')),
            None, None, output.append, Tasks((AgentEvent(EventKind.COMPLETED),)))
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(output[-1])['payload']['result']['stop_code'], 'state_read_failed')

    async def test_successful_query_of_failed_task_returns_zero(self):
        output = []
        code = await execute_command(parse_command(('task', 'result', 'task')),
            None, None, output.append, Tasks((), TaskResult('failed')))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[-1])['execution_status'], 'failed')
