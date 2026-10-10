"""Source gate receipt, atomic correction, recovery and budget counterexamples."""
import asyncio
import json
import threading
import tempfile
import unittest
from dataclasses import replace
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import Mock, patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionResult, Message, ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.source_completion import SourceCompletionSnapshot
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest
from chaos_agent.child_result import collect_child_result
from chaos_agent.source_completion import ChildSourceCompletion, canonical_sources
from tests.agent_app_test_support import _isolated_application


class ScriptedClient:
    def __init__(self, streams, on_completed=None):
        self.streams = list(streams)
        self.calls = 0
        self.on_completed = on_completed

    async def stream(self, *args):
        self.calls += 1
        for event in self.streams.pop(0):
            yield event
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        if self.on_completed is not None:
            await self.on_completed(self.calls)
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        pass


def text(value='Final advisory.'):
    return [ModelEvent(ModelEventKind.TEXT_DELTA, text=value)]


def read(path='a.txt', identifier='source-read'):
    return [ModelEvent(ModelEventKind.TOOL_CALL,
        tool_call=ToolCall(identifier, 'read', {'operation': 'file', 'path': path}))]


class SourceCompletionContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app, self.root, _ = _isolated_application(Path(self.temp.name))
        (self.root / 'a.txt').write_text('Full source\n', encoding='utf-8')
        (self.root / 'empty.txt').write_text('', encoding='utf-8')
        owner = await self.app.sessions.create_thread()
        self.auth = TaskAuthorization(str(self.root), allow_workspace_write=False, allow_local_execute=False)
        self.task = await self.app.sessions.create_task(owner, TaskContract('Deep read-only investigation', self.auth))
        await self.app.sessions.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.app.sessions.register_task_execution(self.task.id, 'source-owner', 123, 45)
        await self.app.sessions.get_or_create_task_budget(owner, 'parent', EngineLimits())
        self.parent = ActionExecutionContext(owner, owner, 'delegate', self.task.id)
        self.runner = self.app.subagents._runner
        self.runner.bind_execution_context(self.parent)
        self.factory = self.runner._factory.__self__
        self.agent = AgentDefinition('source-worker', AgentRole.SEARCH, self.app.mode,
            'Read sources.', ('read_file', 'read_code_slices'), may_write=False)

    async def asyncTearDown(self):
        await self.app.aclose()
        self.temp.cleanup()

    async def run_child(self, streams, required=('a.txt',), tools=4, cancellation=None, on_completed=None):
        self.client = ScriptedClient(streams, on_completed)
        self.factory._client_factory = Mock(return_value=self.client)
        request = ChildRunRequest(self.task.id, 'Read sources', self.agent, 1, 60000, tools, 30,
                                  required_sources=required)
        result = await self.runner.run(request, cancellation or CancellationToken())
        self.child = next(reversed(self.app.subagents._child_threads.values()))
        return result

    def host(self):
        from chaos_agent.restricted_dispatcher import RestrictedDispatcher
        return ChildSourceCompletion(self.app.sessions,
            RestrictedDispatcher(self.factory._dispatcher, self.agent.effective_tools, compact_tools=True,
                                 frozen_authorization=self.auth), self.auth)

    async def test_real_source_guards_run_off_loop_with_context(self):
        from code_agent.workspace.paths import WorkspacePathGuard
        loop_thread = threading.get_ident()
        probe = ContextVar('source-guard-test-context', default=None)
        token = probe.set('frozen-child-context')
        observations = []

        def real_guard(*args, **kwargs):
            observations.append((threading.get_ident(), probe.get()))
            return WorkspacePathGuard(*args, **kwargs)

        try:
            with patch('chaos_agent.source_completion.WorkspacePathGuard', side_effect=real_guard):
                result = await self.run_child([read(), text()])
                self.assertEqual(result.status.value, 'completed')
                self.assertTrue(observations)
                self.assertTrue(all(thread != loop_thread and value == 'frozen-child-context'
                                    for thread, value in observations), observations)
                # Fresh history inspection exercises the actual paired receipt path.
                observations.clear()
                host = self.host()
                self.assertEqual((await host.snapshot(self.child)).completed, ('a.txt',))
                self.assertTrue(observations)
                self.assertTrue(all(thread != loop_thread and value == 'frozen-child-context'
                                    for thread, value in observations), observations)
                # Cached history means this call constructs only the supplied-path Guard.
                observations.clear()
                await host.snapshot(self.child, ('a.txt',))
                self.assertEqual(len(observations), 1)
                self.assertNotEqual(observations[0][0], loop_thread)
                self.assertEqual(observations[0][1], 'frozen-child-context')
        finally:
            probe.reset(token)

    async def test_cancelled_guard_inspection_replays_durable_receipt(self):
        from code_agent.workspace.paths import WorkspacePathGuard
        await self.run_child([read(), text()])
        host = self.host()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        entered, finished = asyncio.Event(), asyncio.Event()
        release = threading.Event()

        def paused_real_guard(*args, **kwargs):
            if threading.get_ident() == loop_thread:
                raise AssertionError('source Guard blocked the event loop')
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(5):
                    raise AssertionError('test did not release source Guard worker')
                return WorkspacePathGuard(*args, **kwargs)
            finally:
                loop.call_soon_threadsafe(finished.set)

        with patch('chaos_agent.source_completion.WorkspacePathGuard', side_effect=paused_real_guard):
            inspection = asyncio.create_task(host.snapshot(self.child))
            try:
                # Await either entry or an immediate failure without waiting for the timeout.
                entry = asyncio.create_task(entered.wait())
                done, _ = await asyncio.wait((inspection, entry), timeout=5,
                                             return_when=asyncio.FIRST_COMPLETED)
                if inspection in done:
                    await inspection
                self.assertTrue(entered.is_set())
                inspection.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await inspection
                self.assertNotIn(self.child, host._history)
            finally:
                release.set()
                entry.cancel()
                await asyncio.gather(entry, return_exceptions=True)
                if entered.is_set():
                    await asyncio.wait_for(finished.wait(), 5)
                if not inspection.done():
                    inspection.cancel()
                    await asyncio.gather(inspection, return_exceptions=True)
        self.assertEqual((await host.snapshot(self.child)).completed, ('a.txt',))

    async def test_empty_file_is_complete_and_final_answer_is_delivered(self):
        result = await self.run_child([read('empty.txt'), text()], ('empty.txt',), tools=1)
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(result.summary, 'Final advisory.')
        self.assertEqual((await self.host().snapshot(self.child)).completed, ('empty.txt',))

    async def test_wrong_path_and_read_failure_remain_missing(self):
        result = await self.run_child([read('missing.txt'), text(), text('Still missing.')])
        self.assertEqual(result.error, 'source_requirements_unmet')
        self.assertEqual(result.result.remaining, ('a.txt',))
        self.assertEqual(self.client.calls, 3)

    async def test_empty_and_whitespace_final_answers_fail_after_full_read(self):
        for value in ('', '   '):
            # A distinct delegate identity keeps the existing child-start guard.
            self.parent = replace(self.parent, request_id='empty-final-' + str(len(value)))
            self.runner.bind_execution_context(self.parent)
            result = await self.run_child([read(), text(value)])
            self.assertEqual(result.status.value, 'failed')
            self.assertEqual(result.error, 'empty_summary')

    async def test_tool_budget_exhaustion_precedes_source_failure(self):
        result = await self.run_child([read('empty.txt'), text()], tools=1)
        self.assertEqual(result.status.value, 'failed')
        self.assertNotEqual(result.error, 'source_requirements_unmet')
        _, correction = await self.app.sessions.source_completion_state(self.child)
        self.assertIsNone(correction)

    async def test_last_available_turn_completes_when_sources_are_read(self):
        original = self.runner._factory
        def limited(*args):
            engine, close = original(*args)
            engine._limits = replace(engine._limits, max_agent_rounds=2)
            return engine, close
        self.runner._factory = limited
        result = await self.run_child([read(), text()], tools=1)
        self.assertEqual(result.status.value, 'completed')

    async def test_round_budget_exhaustion_precedes_source_failure(self):
        original = self.runner._factory
        def limited(*args):
            engine, close = original(*args)
            engine._limits = replace(engine._limits, max_agent_rounds=1)
            return engine, close
        self.runner._factory = limited
        result = await self.run_child([text()])
        self.assertNotEqual(result.error, 'source_requirements_unmet')
        self.assertEqual(self.client.calls, 1)

    async def test_resume_keeps_requirements_and_used_correction(self):
        result = await self.run_child([text('Plan'), text('Still no reads')])
        self.assertEqual(result.error, 'source_requirements_unmet')
        before = await self.app.sessions.source_completion_state(self.child)
        from code_agent.sessions.repository import SQLiteSessionRepository
        reopened = SQLiteSessionRepository(self.app.sessions._base._database.path)
        try:
            restored_host = self.host()
            restored_host.sessions = reopened
            restored = await restored_host.snapshot(self.child)
            self.assertEqual(restored.required, ('a.txt',))
            self.assertEqual(restored.correction_baseline, ())
            with self.assertRaises(ValueError):
                await restored_host.snapshot(self.child, ())
        finally:
            reopened.close()
        client = ScriptedClient([text('Resumed with no new reads')])
        self.factory._client_factory = Mock(return_value=client)
        engine, closer = self.factory.child_engine(self.agent, self.parent, self.auth)
        try:
            with self.assertRaises(ValueError):
                _ = [e async for e in engine.run('resume', thread_id=self.child, required_sources=())]
            self.assertEqual(client.calls, 0)
            events = [e async for e in engine.run('resume', thread_id=self.child)]
            terminal = next(e for e in events if e.kind is EventKind.TASK_RESULT)
            self.assertEqual(terminal.payload['result']['stop_code'], 'source_requirements_unmet')
            self.assertEqual(client.calls, 1)
        finally:
            await closer.aclose()
        self.assertEqual(before, await self.app.sessions.source_completion_state(self.child))

    async def test_bounded_recovery_reads_old_receipt_beyond_context_tail(self):
        await self.run_child([read(), text()])
        for i in range(110):
            await self.app.sessions.append_message(self.child, Message('assistant', 'tail ' + str(i)))
        with patch.object(self.app.sessions._base, 'load_messages', side_effect=AssertionError('full history forbidden')):
            host = self.host()
            self.assertEqual((await host.snapshot(self.child)).completed, ('a.txt',))
            with patch.object(self.app.sessions._base, 'read_history_page', side_effect=AssertionError('no new messages')):
                self.assertEqual((await host.snapshot(self.child)).completed, ('a.txt',))

    async def test_receipt_name_pairing_metadata_and_slices_are_not_proof(self):
        await self.run_child([text(), text()])
        host = self.host()
        full = {'path': 'a.txt', 'text': 'contents', 'total_lines': 1}
        for name, arguments, error, output, metadata, reply_name in (
            ('mcp.docs.read_file', {'path': 'a.txt'}, False, full, {'source_complete': True}, 'mcp.docs.read_file'),
            ('plugin.read_file', {'path': 'a.txt'}, False, full, {'plugin_target': 'read_file'}, 'plugin.read_file'),
            ('read', {'operation': 'slices', 'path': 'a.txt'}, False, full, {}, 'read'),
            ('read_file', {'path': 'a.txt'}, True, full, {}, 'read_file'),
            ('read_file', {'path': 'a.txt'}, False, {**full, 'truncated': True}, {}, 'read_file'),
            ('read_file', {'path': 'a.txt'}, False, full, {}, 'other'),
        ):
            with self.subTest(name=name, output=output):
                call = ToolCall('fake', name, arguments)
                result = ActionResult('fake', name, output, error, metadata)
                message = Message('tool', json.dumps(result.to_dict()), name=reply_name, tool_call_id='fake')
                self.assertIsNone(host._full_read(call, message))

    async def test_parent_receipts_do_not_satisfy_child(self):
        call = ToolCall('parent-read', 'read_file', {'path': 'a.txt'})
        await self.app.sessions.append_message(self.task.thread_id, Message('assistant', tool_calls=(call,)))
        receipt = ActionResult(call.id, call.name, {'path': 'a.txt', 'text': 'Full source', 'total_lines': 1})
        await self.app.sessions.append_message(self.task.thread_id,
            Message('tool', json.dumps(receipt.to_dict()), name=call.name, tool_call_id=call.id))
        result = await self.run_child([text(), text()])
        self.assertEqual(result.error, 'source_requirements_unmet')

    async def test_correction_atomic_rollback_and_long_notice_coverage(self):
        await self.run_child([read(), text()])
        # Use a fresh binding whose requirements are long but workspace-relative.
        child = await self.app.sessions.create_thread(parent_thread_id=self.task.thread_id)
        required = tuple('folder' + str(i) + '/' + 'x' * 990 for i in range(3))
        await self.app.sessions.bind_child_budget(child, self.task.thread_id, self.task.id, 'long-notice',
            max_total_tokens=60000, max_tool_calls=4, required_sources=required)
        host = self.host()
        snapshot = await host.snapshot(child)
        from code_agent.sessions import _thread_content
        append = _thread_content.append_message_record
        def fail(connection, thread, message, timestamp):
            append(connection, thread, message, timestamp)
            raise RuntimeError('atomic write failure')
        with patch.object(_thread_content, 'append_message_record', side_effect=fail):
            with self.assertRaises(RuntimeError):
                await host.correct(child, snapshot)
        self.assertEqual((await self.app.sessions.history_stats(child))['message_count'], 0)
        self.assertIsNone((await self.app.sessions.source_completion_state(child))[1])
        notices = await host.correct(child, snapshot)
        self.assertTrue(all(len(m.content) <= 1000 for m in notices))
        combined = ''.join(m.content for m in notices)
        self.assertTrue(all(path in combined for path in required))

    async def test_invalid_sources_rejected_before_provider_allocation(self):
        from code_agent.workspace.errors import WorkspaceError
        for value in ('../outside', '/absolute', 'C:/absolute', '.git/config'):
            with self.subTest(path=value), self.assertRaises((ValueError, WorkspaceError)):
                canonical_sources((value,), self.auth)

    async def test_source_final_summary_keeps_latest_answer(self):
        request = ChildRunRequest('parent', 'read', self.agent, 1, 60000, 4, 30, required_sources=('a.txt',))
        async def events():
            for content in ('P' * 16384, 'FINAL_ANALYSIS_MARKER'):
                yield AgentEvent(EventKind.MESSAGE_ADDED, {'message': Message('assistant', content).to_dict()})
            yield AgentEvent(EventKind.COMPLETED, {})
        result = await collect_child_result(events(), request)
        self.assertEqual(result.summary, 'FINAL_ANALYSIS_MARKER')
        legacy = await collect_child_result(events(), replace(request, required_sources=()))
        self.assertEqual(legacy.summary, 'P' * 16384)

    async def test_cancel_during_correction_preserves_cancelled_terminal(self):
        token = CancellationToken()
        original = ChildSourceCompletion.correct
        async def cancel_after(host, *args):
            notices = await original(host, *args)
            token.cancel('source correction cancelled')
            return notices
        with patch.object(ChildSourceCompletion, 'correct', cancel_after):
            result = await self.run_child([text('Plan')], cancellation=token)
        self.assertEqual(result.status.value, 'cancelled')
        self.assertEqual(self.client.calls, 1)
        self.assertIsNotNone((await self.app.sessions.source_completion_state(self.child))[1])

    async def test_frozen_binding_conflict_and_legacy_missing_field(self):
        await self.run_child([read(), text()])
        with self.assertRaises(ValueError):
            await self.app.sessions.bind_child_budget(self.child, self.task.thread_id, self.task.id, 'delegate',
                max_total_tokens=60000, max_tool_calls=4, required_sources=())
        child = await self.app.sessions.create_thread(parent_thread_id=self.task.thread_id)
        await self.app.sessions.bind_child_budget(child, self.task.thread_id, self.task.id, 'legacy',
            max_total_tokens=60000, max_tool_calls=4)
        def legacy(connection):
            row = connection.execute("SELECT id,metadata FROM checkpoints WHERE thread_id=? AND label='context:child_budget'", (child,)).fetchone()
            payload = json.loads(row['metadata'])
            payload.pop('required_sources')
            connection.execute('UPDATE checkpoints SET metadata=? WHERE id=?', (json.dumps(payload), row['id']))
        await self.app.sessions._base._database.write(legacy)
        await self.app.sessions.bind_child_budget(child, self.task.thread_id, self.task.id, 'legacy',
            max_total_tokens=60000, max_tool_calls=4)
        self.assertEqual((await self.app.sessions.source_completion_state(child))[0], ())

    async def test_no_requirements_preserves_one_turn_completion(self):
        result = await self.run_child([text('Ordinary answer')], required=())
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(self.client.calls, 1)

    async def test_history_epoch_change_invalidates_cached_read(self):
        await self.run_child([read(), text()])
        host = self.host()
        self.assertEqual((await host.snapshot(self.child)).completed, ('a.txt',))
        def invalidate(connection):
            row = connection.execute("SELECT sequence,payload FROM messages WHERE thread_id=? AND json_extract(payload,'$.role')='tool'", (self.child,)).fetchone()
            message = Message.from_dict(json.loads(row['payload']))
            result = ActionResult.from_dict(json.loads(message.content))
            failed = replace(result, is_error=True)
            updated = replace(message, content=json.dumps(failed.to_dict()))
            connection.execute('UPDATE messages SET payload=? WHERE sequence=?', (json.dumps(updated.to_dict()), row['sequence']))
        await self.app.sessions._base._database.write(invalidate)
        self.assertEqual((await host.snapshot(self.child)).completed, ())

    async def _shared_budget_priority(self, kind):
        async def consume(calls):
            if calls != 2:
                return
            budget = await self.app.sessions.load_task_budget(self.task.id)
            if kind == 'tokens':
                await self.app.sessions.consume_task_usage(self.task.id,
                    Usage(budget.limits.max_total_tokens - budget.input_tokens - budget.output_tokens, 0))
            else:
                increments = {'model_turns': budget.limits.max_agent_rounds - budget.model_turns} if kind == 'rounds' else {
                    'tool_calls': budget.limits.max_tool_calls - budget.tool_calls}
                reservation = await self.app.sessions.reserve_task_budget(self.task.thread_id, **increments)
                self.assertTrue(reservation.accepted)
        result = await self.run_child([text('Plan'), text('No source progress')], on_completed=consume)
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(result.status.value, 'failed')
        self.assertNotEqual(result.error, 'source_requirements_unmet')
        self.assertIsNotNone((await self.app.sessions.source_completion_state(self.child))[1])
        errors = [e for e in await self.app.sessions.load_events(self.child) if e.kind is EventKind.ERROR]
        self.assertEqual(errors[-1].payload['error_type'], 'EngineLimitError')

    async def test_shared_owner_round_exhaustion_precedes_source_failure(self):
        await self._shared_budget_priority('rounds')

    async def test_shared_owner_tool_exhaustion_precedes_source_failure(self):
        await self._shared_budget_priority('tools')

    async def test_shared_owner_token_exhaustion_precedes_source_failure(self):
        await self._shared_budget_priority('tokens')

    async def test_existing_quick_budget_is_not_migrated_by_team_selection(self):
        from code_agent.core.limits import BudgetLeaseTier
        thread = await self.app.sessions.create_thread()
        limits = EngineLimits(max_agent_rounds=12, max_tool_calls=40)
        old = await self.app.sessions.get_or_create_task_budget(thread, 'model', limits, BudgetLeaseTier.QUICK)
        restored = await self.app.sessions.get_or_create_task_budget(thread, 'model', limits, BudgetLeaseTier.STANDARD)
        self.assertEqual(restored, old)
