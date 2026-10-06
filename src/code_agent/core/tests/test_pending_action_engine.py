"""The shared engine checks durable history, including direct non-task entry."""
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

from code_agent.core.engine import AgentEngine
from code_agent.core.pending_actions import pending_calls
from code_agent.core.errors import PendingActionError, ModelStreamError
from code_agent.core.models import Message, ToolCall, ActionResult, ModelEvent, ModelEventKind
from code_agent.core.tests._engine_support import FakeActionDispatcher, FakeContextBuilder, FakeModelClient
from code_agent.sessions.repository import SQLiteSessionRepository


class PendingActionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_engine_checks_reopened_durable_history_before_model_or_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sessions.sqlite3'
            repo = SQLiteSessionRepository(path)
            thread = await repo.create_thread()
            await repo.append_message(thread, Message('assistant', tool_calls=(ToolCall('unknown', 'write_file', {'path': 'a.txt', 'content': 'new'}),)))
            repo.close()
            repo = SQLiteSessionRepository(path)
            model, actions, context = FakeModelClient([]), FakeActionDispatcher(), FakeContextBuilder()
            engine = AgentEngine(model, context, actions, repo, require_verification=False)
            before = await repo.load_events(thread)
            for _ in range(2):
                with self.assertRaises(PendingActionError):
                    async for _ in engine.run('continue', thread_id=thread):
                        pass
            self.assertEqual(model.calls, [])
            self.assertEqual(actions.requests, [])
            self.assertEqual(context.calls, [])
            self.assertEqual(await repo.load_events(thread), before)
            script = '''import asyncio, sys
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.core.engine import AgentEngine
from code_agent.core.errors import PendingActionError
from code_agent.core.tests._engine_support import FakeActionDispatcher, FakeContextBuilder, FakeModelClient
async def main():
    repo = SQLiteSessionRepository(sys.argv[1])
    model, actions = FakeModelClient([]), FakeActionDispatcher()
    engine = AgentEngine(model, FakeContextBuilder(), actions, repo, require_verification=False)
    try:
        async for _ in engine.run('continue', thread_id=sys.argv[2]): pass
    except PendingActionError:
        assert not model.calls and not actions.requests
        print('durable-blocked-after-process-restart')
    else: raise AssertionError('restart bypassed pending action')
asyncio.run(main())
'''
            restarted = subprocess.run([sys.executable, '-c', script, str(path), thread], capture_output=True, text=True, timeout=30)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            self.assertIn('durable-blocked-after-process-restart', restarted.stdout)

    async def test_known_old_call_id_cannot_be_dispatched_after_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = SQLiteSessionRepository(Path(temporary) / 'sessions.sqlite3')
            thread = await repo.create_thread()
            call = ToolCall('old', 'read_file', {'path': 'a.txt'})
            await repo.append_message(thread, Message('assistant', tool_calls=(call,)))
            await repo.append_message(thread, Message('tool', name=call.name, tool_call_id=call.id, content=json.dumps(ActionResult(call.id, call.name, 'known').to_dict())))
            model = FakeModelClient([[ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED)]])
            actions = FakeActionDispatcher()
            engine = AgentEngine(model, FakeContextBuilder(), actions, repo, require_verification=False)
            with self.assertRaisesRegex(ModelStreamError, 'reused'):
                async for _ in engine.run('continue', thread_id=thread):
                    pass
            self.assertEqual(actions.requests, [])
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(pending_calls(await repo.load_messages(thread)), ())
            fresh = ToolCall('new', 'read_file', {'path': 'a.txt'})
            engine._model = FakeModelClient([[ModelEvent(ModelEventKind.TOOL_CALL, tool_call=fresh), ModelEvent(ModelEventKind.COMPLETED)],
                [ModelEvent(ModelEventKind.TEXT_DELTA, text='Read complete'), ModelEvent(ModelEventKind.COMPLETED)]])
            actions.outcomes = [ActionResult(fresh.id, fresh.name, 'data')]
            async for _ in engine.run('continue', thread_id=thread):
                pass
            self.assertEqual([request.id for request in actions.requests], ['new'])
