import tempfile
import asyncio
import unittest
from pathlib import Path
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskStatus
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository


class DurableTaskResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_stop_result_requires_current_execution_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = (root / 'sessions.sqlite3').resolve()
            self.assertTrue(path.is_relative_to(root))
            sessions = SQLiteSessionRepository(path)
            controller = ForegroundTaskController(AgentController(type('Runner', (), {'run': None})()),
                                                  sessions, root)
            task = await controller.start('Inspect sources')
            stream = controller.events(task.id)
            started = await anext(stream)
            token = CancellationToken()
            token.cancel('user stopped task')
            controller._tokens[task.id] = token
            await sessions.transition_task(task.id, TaskStatus.FAILED, 'user stopped task')
            result = await controller._record_cancelled_result(task.id,
                AgentEvent(EventKind.CANCELLED, {'reason': 'user stopped task'}), 'different-run')
            self.assertNotEqual(started.payload['run_instance_id'], 'different-run')
            self.assertEqual(result.kind, EventKind.TASK_RESULT)
            self.assertEqual((await controller.result(task.id)).exit_code(), 1)
            await stream.aclose()

    async def test_explicit_stop_both_durable_orders_agree_without_timing(self):
        for failed_first in (True, False):
            with self.subTest(failed_first=failed_first), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                path = (root / 'sessions.sqlite3').resolve()
                self.assertTrue(path.is_relative_to(root))
                sessions = SQLiteSessionRepository(path)
                entered, allow_cancel, failed_pending, allow_failed = (
                    asyncio.Event() for _ in range(4))
                class Runner:
                    async def run(self, *args, cancellation=None, **kwargs):
                        entered.set()
                        await cancellation.wait_async()
                        await allow_cancel.wait()
                        yield AgentEvent(EventKind.CANCELLED, {'reason': 'user stopped task'})
                transition = sessions.transition_task
                async def ordered_transition(task_id, status, reason=None):
                    if status is TaskStatus.FAILED and not failed_first:
                        failed_pending.set()
                        await allow_failed.wait()
                    return await transition(task_id, status, reason)
                sessions.transition_task = ordered_transition
                controller = ForegroundTaskController(AgentController(Runner()), sessions, root)
                task = await controller.start('Inspect sources')
                stream = controller.events(task.id)
                await anext(stream)
                draining = asyncio.create_task(anext(stream))
                await entered.wait()
                stopping = asyncio.create_task(controller.stop(task.id))
                if failed_first:
                    await stopping
                    self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.FAILED)
                    allow_cancel.set()
                    event = await draining
                else:
                    await failed_pending.wait()
                    allow_cancel.set()
                    event = await draining
                    self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.RUNNING)
                    allow_failed.set()
                    await stopping
                self.assertEqual(event.kind, EventKind.CANCELLED)
                self.assertEqual(event.payload['result']['execution_status'], 'cancelled')
                await stream.aclose()
                self.assertEqual((await controller.result(task.id)).exit_code(), 130)
                reopened = ForegroundTaskController(AgentController(Runner()),
                                                     SQLiteSessionRepository(path), root)
                self.assertEqual((await reopened.result(task.id)).exit_code(), 130)

    async def test_late_cancel_cannot_cover_completed_or_accepted_partial(self):
        from code_agent.core.task_result import TaskResult
        for terminal in (TaskStatus.COMPLETED, TaskStatus.ACCEPTED_PARTIAL, TaskStatus.SUPERSEDED):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                path = (root / 'sessions.sqlite3').resolve()
                self.assertTrue(path.is_relative_to(root))
                sessions = SQLiteSessionRepository(path)
                entered, emit = asyncio.Event(), asyncio.Event()
                class Runner:
                    async def run(self, *args, cancellation=None, **kwargs):
                        entered.set()
                        await emit.wait()
                        yield AgentEvent(EventKind.CANCELLED, {'reason': 'late cancellation'})
                controller = ForegroundTaskController(AgentController(Runner()), sessions, root)
                task = await controller.start('Inspect sources')
                stream = controller.events(task.id)
                await anext(stream)
                draining = asyncio.create_task(anext(stream))
                await entered.wait()
                if terminal is TaskStatus.ACCEPTED_PARTIAL:
                    await sessions.transition_task(task.id, TaskStatus.VERIFYING)
                    await controller.accept_partial(task.id)
                else:
                    await sessions.transition_task(task.id, terminal)
                prior = await controller.result(task.id)
                emit.set()
                event = await draining
                self.assertEqual(event.kind, EventKind.TASK_RESULT)
                self.assertEqual(TaskResult.from_dict(event.payload['result']), prior)
                await stream.aclose()
                self.assertEqual((await controller.result(task.id)).to_dict(), prior.to_dict())

    async def test_cancellation_during_verifying_survives_query(self):
        from code_agent.core.cancellation import CancellationError
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / 'sessions.sqlite3')
            class Runner:
                async def run(self, *args, task=None, **kwargs):
                    await sessions.transition_task(task.id, TaskStatus.VERIFYING)
                    raise CancellationError('cancel during verification')
                    yield None
            controller = ForegroundTaskController(AgentController(Runner()), sessions, root)
            task = await controller.start('Inspect sources')
            events = [event async for event in controller.events(task.id)]
            self.assertEqual(events[-1].kind, EventKind.CANCELLED)
            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.VERIFYING)
            self.assertEqual((await controller.result(task.id)).execution_status, 'cancelled')
            self.assertEqual((await controller.result(task.id)).exit_code(), 130)
            await sessions.transition_task(task.id, TaskStatus.PAUSED)
            self.assertEqual((await controller.result(task.id)).execution_status, 'cancelled')
            resumed = controller.events(task.id)
            await anext(resumed)
            self.assertEqual((await controller.result(task.id)).execution_status, 'interrupted')
            await resumed.aclose()

    async def test_stop_cancel_event_query_and_reopen_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / 'sessions.sqlite3')
            class Runner:
                async def run(self, *args, cancellation=None, **kwargs):
                    await cancellation.wait_async()
                    yield AgentEvent(EventKind.CANCELLED, {'reason': 'user stopped task'})
            controller = ForegroundTaskController(AgentController(Runner()), sessions, root)
            task = await controller.start('Inspect sources')
            stream = controller.events(task.id)
            await anext(stream)
            import asyncio
            draining = asyncio.create_task(anext(stream))
            while task.id not in controller._tokens:
                await asyncio.sleep(0)
            await controller.stop(task.id)
            event = await draining
            self.assertEqual(event.kind, EventKind.CANCELLED)
            await stream.aclose()
            self.assertEqual((await controller.result(task.id)).exit_code(), 130)
            reopened = ForegroundTaskController(AgentController(Runner()),
                SQLiteSessionRepository(root / 'sessions.sqlite3'), root)
            self.assertEqual((await reopened.result(task.id)).execution_status, 'cancelled')
            self.assertEqual((await sessions.load_task(task.id)).status, TaskStatus.FAILED)

    async def test_old_completed_record_remains_unknown_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / 'sessions.sqlite3')
            controller = ForegroundTaskController(AgentController(type('Runner', (), {'run': None})()), sessions, root)
            task = await controller.start('Explain sources')
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.transition_task(task.id, TaskStatus.COMPLETED)
            result = await controller.result(task.id)
            self.assertEqual(result.execution_status, 'completed')
            self.assertEqual(result.verification_status, 'unknown')

    async def test_recorded_verification_requires_same_generation_and_task_version(self):
        from dataclasses import replace
        from code_agent.core.task_result import TaskResult
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = SQLiteSessionRepository(root / 'sessions.sqlite3')
            controller = ForegroundTaskController(AgentController(type('Runner', (), {'run': None})()), sessions, root)
            task = await controller.start('Inspect sources')
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            task = await sessions.transition_task(task.id, TaskStatus.COMPLETED)
            state = await sessions.load_task_state(task.thread_id)
            await sessions.append_event(task.thread_id, AgentEvent(EventKind.TASK_RESULT, {
                'task_id': task.id, 'task_updated_at': task.updated_at.isoformat(),
                'result_generation': state.code_generation, 'result_subject_hash': state.subject_hash,
                'result': TaskResult('completed', verification_status='verified').to_dict()}))
            self.assertEqual((await controller.result(task.id)).verification_status, 'verified')
            await sessions.save_task_state(task.thread_id, replace(state, code_generation=state.code_generation + 1))
            self.assertEqual((await controller.result(task.id)).verification_status, 'unknown')
