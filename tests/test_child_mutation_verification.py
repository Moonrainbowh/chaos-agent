"""Read-only product audit: temporary real production child and Sessions."""
import asyncio
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
import psutil
from tests.agent_app_test_support import _isolated_application
from tests.test_child_execution_scope import ChildClient
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionRequest, ActionResult, ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest
from code_agent.verification.task_service import LedgerTaskVerificationService

class Writer(ChildClient):
    async def stream(self, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall('child-write', 'write_file', {'path': 'new_module.py', 'content': 'VALUE = 2\n'}))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text='All tests passed')
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

async def reproduce_child_mutation():
    with tempfile.TemporaryDirectory() as temporary:
        app, workspace, _ = _isolated_application(Path(temporary))
        sessions = app.sessions
        owner = await sessions.create_thread()
        task = await sessions.create_task(owner, TaskContract('Update documentation and code', TaskAuthorization(str(workspace), allow_workspace_write=True)))
        await sessions.transition_task(task.id, TaskStatus.RUNNING)
        await sessions.get_or_create_task_budget(owner, 'parent', EngineLimits(10, 10, 2, 200000))
        process = psutil.Process(os.getpid())
        await sessions.register_task_execution(task.id, 'audit-instance', process.pid, process.create_time())
        try:
            service = LedgerTaskVerificationService(workspace, sessions)
            state = await service.prepare(task, await sessions.load_task_state(owner))
            (workspace / 'README.md').write_text('documentation\n')
            request = ActionRequest('parent-doc', 'write_file', {'path': 'README.md'})
            result = ActionResult(request.id, request.name, {'path': 'README.md'})
            state = await sessions.reduce_task_state(owner, request, result)
            state = await service.record_action(task, request, result, state)
            await service.suggest_verification(task, state)
            before = await service.assess(task, state)
            writer = Writer()
            runner = app.subagents._runner
            runner._factory.__self__._client_factory = lambda *args, **kwargs: writer
            runner.bind_execution_context(ActionExecutionContext(owner, owner, 'delegate-audit', task.id))
            agent = AgentDefinition('writer', AgentRole.SUBAGENT, app.mode, 'write code', ('write_file',), may_write=True)
            run_request = ChildRunRequest(task.id, 'Write new_module.py', agent, 1, 100000, 4, 30)
            child_result = await runner.run(run_request, CancellationToken())
            child = app.subagents._child_threads[run_request.run_id]
            parent_state = await sessions.load_task_state(owner)
            child_state = await sessions.load_task_state(child)
            after = await service.assess(task, parent_state)
            evidence = await sessions.list_completed_verification_evidence(task.id)
            task = await sessions.transition_task(task.id, TaskStatus.VERIFYING)
            try:
                finalized = await service.finalize(task, after)
                final_status = finalized.status.value
            except (RuntimeError, ValueError) as error:
                final_status = 'blocked: ' + str(error)
            output = dict(file_exists=(workspace/'new_module.py').is_file(),
                file_content=(workspace/'new_module.py').read_text() if (workspace/'new_module.py').exists() else None,
                child_status=child_result.status.value,
                parent_files_changed=list(parent_state.files_changed), child_files_changed=list(child_state.files_changed),
                before_generation=before.generation, after_generation=after.generation,
                same_subject=before.subject_hash == after.subject_hash,
                before_completion=before.assessment.kind.value, after_completion=after.assessment.kind.value,
                evidence_count=len(evidence), before_run=before.verification_run_id, after_run=after.verification_run_id,
                parent_finalized_status=final_status)
            return output
        finally:
            await sessions.release_task_execution(task.id, 'audit-instance')
            await app.aclose()


import unittest

class ChildMutationVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_path_equal_generation_snapshot_race_rejects_stale_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            app, workspace, _ = _isolated_application(Path(temporary))
            sessions = app.sessions
            owner = await sessions.create_thread()
            task = await sessions.create_task(owner, TaskContract('Edit docs', TaskAuthorization(str(workspace))))
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.get_or_create_task_budget(owner, 'model', EngineLimits())
            process = psutil.Process(os.getpid())
            await sessions.register_task_execution(task.id, 'race-owner', process.pid, process.create_time())
            child = await sessions.create_thread(parent_thread_id=owner)
            await sessions.bind_child_budget(child, owner, task.id, 'race-delegate', max_total_tokens=100000, max_tool_calls=4)
            try:
                service = LedgerTaskVerificationService(workspace, sessions)
                await service.prepare(task, await sessions.load_task_state(owner))
                target = workspace / 'README.md'
                target.write_text('before\n')
                request = ActionRequest('parent-write', 'write_file', {'path': 'README.md'})
                result = ActionResult(request.id, request.name, {})
                state = await sessions.reduce_task_state(owner, request, result)
                state = await service.record_action(task, request, result, state)
                await service.suggest_verification(task, state)
                before = await service.assess(task, state)
                self.assertEqual(before.assessment.kind.value, 'verified')
                from code_agent.workspace.subject import snapshot_subject
                def child_write():
                    target.write_text('concurrent child mutation\n')
                    child_request = ActionRequest('child-write', 'write_file', {'path': 'README.md'})
                    asyncio.run(sessions.reduce_task_state(child, child_request,
                        ActionResult(child_request.id, child_request.name, {})))
                with ThreadPoolExecutor(max_workers=1) as workers:
                    def gated_snapshot(*args, **kwargs):
                        stale = snapshot_subject(*args, **kwargs)
                        workers.submit(child_write).result(timeout=5)
                        return stale
                    with patch('code_agent.verification.task_service.snapshot_subject', side_effect=gated_snapshot):
                        with self.assertRaisesRegex(ValueError, 'changed during subject snapshot'):
                            await service._snapshot(task, state, state.code_generation + 1)
                current = await sessions.load_task_state(owner)
                self.assertEqual(current.code_generation, state.code_generation + 1)
                self.assertEqual(current.files_changed, state.files_changed)
                self.assertEqual(current.subject_hash, '')
                after = await service.assess(task, current)
                self.assertEqual(after.assessment.kind.value, 'unverified')
                self.assertIsNone(after.verification_run_id)
                await sessions.transition_task(task.id, TaskStatus.VERIFYING)
                with self.assertRaisesRegex(ValueError, 'generation|subject'):
                    await service.finalize(task, before)
            finally:
                await sessions.release_task_execution(task.id, 'race-owner')
                await app.aclose()

    async def test_production_child_code_write_cannot_reuse_document_attestation(self):
        result = await reproduce_child_mutation()
        self.assertTrue(result['file_exists'])
        self.assertEqual(result['child_status'], 'completed')
        self.assertIn('new_module.py', result['parent_files_changed'])
        self.assertGreater(result['after_generation'], result['before_generation'])
        self.assertFalse(result['same_subject'])
        self.assertEqual(result['before_completion'], 'verified')
        self.assertEqual(result['after_completion'], 'unverified')
        self.assertTrue(result['parent_finalized_status'].startswith('blocked:'))
