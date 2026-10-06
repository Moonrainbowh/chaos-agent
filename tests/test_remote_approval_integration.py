"""Actual application policy/dispatch and ASGI response, without a provider call."""
import asyncio
import os
import tempfile
import unittest
import sqlite3
import json
import subprocess
from contextlib import ExitStack, closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
import psutil

from chaos_agent.app import create_application
from chaos_agent.remote.server import create_host_app
from chaos_agent.remote.pairing import PairingStore
from code_agent.config.loader import load_runtime_config
from code_agent.providers.config import ConfiguredApiKey
from code_agent.project_launcher.store import ProjectStore
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken, CancellationError
from code_agent.core.models import ActionRequest, ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.core.task_state import TaskState


class NoProviderCalls:
    async def stream(self, *args):
        raise AssertionError('This integration test must not call a provider')
        yield

    async def aclose(self):
        pass


class ProtectedWriteModel:
    """Exactly two offline rounds; the real Foreground owns all actions and gates."""
    def __init__(self):
        self.calls = 0

    async def stream(self, *args):
        self.calls += 1
        if self.calls == 1:
            yield ModelEvent(ModelEventKind.TOOL_CALL, tool_call=ToolCall(
                'foreground-protected-write', 'write_file',
                {'path': '.env', 'content': 'S12_FOREGROUND_FIXTURE_ONLY'}))
        else:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text='Fixture written. Verification remains for the Host to decide.')
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 5))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        pass


class RemoteApprovalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.container = Path(self.temp.name).resolve()
        self.root = self.container / 'project'
        self.root.mkdir()
        self.state = self.container / 'state'
        self.database = self.state / 'sessions.sqlite3'
        self.paths = [self.database, self.state / 'permission-rules.sqlite3',
            self.state / 'projects.json', self.state / 'remote' / 'devices.json',
            self.state / 'workspaces', self.container / 'profile', self.container / 'local']
        assert all(path.is_absolute() and path.resolve().is_relative_to(self.container) for path in self.paths)
        runtime = load_runtime_config(env={
            'CHAOS_CONFIG': str(self.container / 'missing.toml'), 'CHAOS_API': 'responses',
            'CHAOS_MODEL': 'gpt-4.1', 'CHAOS_BASE_URL': 'https://offline.example.test',
            'CHAOS_API_KEY_ENV': 'KEY', 'CHAOS_APPROVAL_MODE': 'ask',
            'CHAOS_ALLOW_SENSITIVE_PATHS': 'true',
        })
        runtime = replace(runtime, profiles=tuple(replace(profile,
            provider=replace(profile.provider, api_key_env=None,
                api_key_source=ConfiguredApiKey('offline-fixture-key'))) for profile in runtime.profiles))
        self.patches = ExitStack()
        self.patches.enter_context(patch.dict(os.environ, {
            'USERPROFILE': str(self.container / 'profile'), 'LOCALAPPDATA': str(self.container / 'local'),
            'CHAOS_WORKSPACE_MODE': 'direct', 'CHAOS_DEBUG_TRACE': '0',
            'CHAOS_STRUCTURED_VERIFICATION': '1'}))
        for name, value in (('_session_path', self.database), ('_product_state_root', self.state),
                            ('_workspace_storage_path', self.state / 'workspaces'),
                            ('load_runtime_config', runtime), ('_model_client', NoProviderCalls())):
            self.patches.enter_context(patch('chaos_agent.app.' + name, return_value=value))
        # Assertions precede each construction, including actual application's repositories.
        assert self.database.parent == self.state and self.state.is_relative_to(self.container)
        self.application = create_application(self.root)
        assert self.paths[2].parent == self.state
        project_store = ProjectStore(self.paths[2])
        assert self.paths[3].is_relative_to(self.container)
        self.pairing = PairingStore(self.paths[3])
        credential = self.pairing.pair(self.pairing.issue_token())
        self.host, _ = create_host_app(self.application, pairing=self.pairing, project_store=project_store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.host),
            base_url='http://isolated.test', headers={'authorization': 'Bearer ' + credential})
        self.thread = await self.application.sessions.create_thread()
        self.task = await self.application.sessions.create_task(self.thread,
            TaskContract('S12 fixture write', TaskAuthorization.local_workspace(str(self.root))))
        await self.application.sessions.transition_task(self.task.id, TaskStatus.RUNNING)
        process = psutil.Process(os.getpid())
        await self.application.sessions.register_task_execution(self.task.id, 'integration-owner', process.pid, process.create_time())
        self.token = CancellationToken()
        self.waiting = None

    async def asyncTearDown(self):
        self.token.cancel('integration fixture cleanup')
        if self.waiting is not None:
            await asyncio.gather(self.waiting, return_exceptions=True)
        await self.client.aclose()
        await self.application.sessions.release_task_execution(self.task.id, 'integration-owner')
        await self.host.state.remote_tasks.aclose()
        await self.application.aclose()
        self.patches.close()
        self.temp.cleanup()

    async def start_approval(self):
        request = ActionRequest('protected-write', 'write_file', {'path': '.env', 'content': 'S12_FIXTURE_ONLY'})
        context = ActionExecutionContext(self.thread, self.thread, request.id, self.task.id)
        self.waiting = asyncio.create_task(self.application.dispatcher.dispatch(request, self.token,
            self.task.contract.authorization, execution_context=context))
        # The real broker queue is only a deterministic arrival barrier, not the answer surface.
        local = await asyncio.wait_for(self.application.approvals.next_request(), 5)
        self.assertEqual(local.request_id, request.id)
        self.assertFalse((self.root / '.env').exists())
        response = await self.client.get(f'/sessions/{self.thread}/requests')
        self.assertEqual(response.status_code, 200, response.text)
        card = response.json()['requests'][0]
        self.assertEqual(card['task_id'], self.task.id)
        self.assertEqual(card['action_id'], request.id)
        self.assertEqual(card['owner_instance_id'], 'integration-owner')
        self.assertEqual(card['workspace_root'].casefold(), str(self.root).casefold())
        self.assertNotIn('S12_FIXTURE_ONLY', str(card['preview']))
        return card

    def body(self, card, **extra):
        return {**{key: card[key] for key in ('action_digest', 'state_version', 'owner_instance_id')},
                'approved': True, **extra}

    async def answer(self, card, *, task_id=None, **extra):
        return await self.client.post(f'/tasks/{task_id or self.task.id}/requests/{card["request_id"]}/respond',
                                      json=self.body(card, **extra))

    async def test_approve_reaches_original_dispatcher_once_and_replay_has_no_effect(self):
        card = await self.start_approval()
        approved = await self.answer(card)
        self.assertEqual(approved.status_code, 200, approved.text)
        result = await asyncio.wait_for(self.waiting, 5)
        self.assertFalse(result.is_error, result.to_dict())
        self.assertEqual((self.root / '.env').read_text(), 'S12_FIXTURE_ONLY')
        assert self.database.resolve().is_relative_to(self.container)
        with closing(sqlite3.connect(self.database)) as connection:
            executed = connection.execute("SELECT COUNT(*) FROM workspace_mutations WHERE task_id=? AND request_id=? AND status='completed'",
                (self.task.id, 'protected-write')).fetchone()[0]
        self.assertEqual(executed, 1)
        before = (self.root / '.env').stat().st_mtime_ns
        replay = await self.answer(card)
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual((self.root / '.env').stat().st_mtime_ns, before)
        assert self.database.resolve().is_relative_to(self.container)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_mutations WHERE task_id=? AND request_id=? AND status='completed'",
                (self.task.id, 'protected-write')).fetchone()[0], executed)
        records = await self.application.sessions.list_approval_requests(self.task.id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['status'], 'approved')

    async def test_denial_never_writes(self):
        card = await self.start_approval()
        denied = await self.answer(card, approved=False)
        self.assertEqual(denied.status_code, 200, denied.text)
        result = await asyncio.wait_for(self.waiting, 5)
        self.assertTrue(result.is_error)
        self.assertFalse((self.root / '.env').exists())

    async def test_cross_task_and_wrong_version_cannot_unlock_then_valid_answer_can(self):
        card = await self.start_approval()
        other_thread = await self.application.sessions.create_thread()
        other = await self.application.sessions.create_task(other_thread,
            TaskContract('other', TaskAuthorization.local_workspace(str(self.root))))
        foreign = await self.answer(card, task_id=other.id)
        self.assertEqual(foreign.status_code, 404, foreign.text)
        stale = await self.answer(card, state_version='0' * 64)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertFalse(self.waiting.done())
        self.assertFalse((self.root / '.env').exists())
        valid = await self.answer(card)
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertFalse((await asyncio.wait_for(self.waiting, 5)).is_error)

    async def test_actual_state_drift_and_device_revocation_keep_file_absent(self):
        card = await self.start_approval()
        await self.application.sessions.save_task_state(self.thread,
            replace(TaskState.empty(), code_generation=1, subject_hash='changed'))
        stale = await self.answer(card)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertFalse((self.root / '.env').exists())
        self.pairing.revoke()
        revoked = await self.answer(card)
        self.assertEqual(revoked.status_code, 401, revoked.text)
        self.assertFalse(self.waiting.done())
        self.assertFalse((self.root / '.env').exists())

    async def test_complete_foreground_rounds_keep_guard_capability_through_wrappers(self):
        await self.application.sessions.release_task_execution(self.task.id, 'integration-owner')
        await self.application.sessions.transition_task(self.task.id, TaskStatus.FAILED, 'handcrafted fixture retired')
        model = ProtectedWriteModel()
        self.application.controller._engine._model.model = model
        projects = await self.client.get('/projects')
        self.assertEqual(projects.status_code, 200, projects.text)
        project = next(row for row in projects.json()['projects'] if row['path'] == str(self.root))
        created = await self.client.post('/sessions', json={'project_id': project['id']})
        self.assertEqual(created.status_code, 200, created.text)
        session_id = created.json()['session_id']
        started = await self.client.post(f'/sessions/{session_id}/messages',
            json={'prompt': 'Modify .env configuration file with the requested fixture content'})
        self.assertEqual(started.status_code, 200, started.text)
        task_id = started.json()['task_id']
        local = await asyncio.wait_for(self.application.approvals.next_request(), 10)
        self.assertEqual(local.request_id, 'foreground-protected-write')
        self.assertFalse((self.root / '.env').exists())
        pending = await self.client.get(f'/sessions/{session_id}/requests')
        self.assertEqual(pending.status_code, 200, pending.text)
        card = next(row for row in pending.json()['requests'] if row['kind'] == 'approval' and row['status'] == 'pending')
        approved = await self.answer(card, task_id=task_id)
        self.assertEqual(approved.status_code, 200, approved.text)
        run = self.host.state.remote_tasks._active
        self.assertEqual(run.task_id, task_id)
        await asyncio.wait_for(asyncio.shield(run.runner), 20)
        self.assertEqual((self.root / '.env').read_text(), 'S12_FOREGROUND_FIXTURE_ONLY')
        self.assertEqual(model.calls, 2)
        assert self.database.resolve().is_relative_to(self.container)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_mutations WHERE task_id=? AND request_id=? AND status='completed'",
                (task_id, 'foreground-protected-write')).fetchone()[0], 1)
        state = await self.application.sessions.load_task_state(session_id)
        self.assertEqual(state.code_generation, 1)
        self.assertTrue(state.subject_hash)
        result = await self.application.tasks.result(task_id)
        self.assertIn(result.execution_status, {'waiting_decision', 'completed'}, result.to_dict())
        self.assertNotIn('SensitivePathError', str(result.to_dict()))
        if result.execution_status == 'waiting_decision':
            self.assertNotEqual(result.verification_status, 'verified')
            decisions = await self.client.get(f'/sessions/{session_id}/requests')
            self.assertEqual(decisions.status_code, 200, decisions.text)
            partial = next(row for row in decisions.json()['requests'] if row['kind'] == 'decision'
                and row['status'] == 'pending' and row['preview']['operation'] == 'accept_partial')
            accepted = await self.answer(partial, task_id=task_id)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            final = await self.application.tasks.result(task_id)
            self.assertEqual(final.execution_status, 'accepted_partial')
            self.assertNotEqual(final.verification_status, 'verified')
        else:
            from code_agent.verification.evidence import EvidenceProvenance
            evidence = await self.application.sessions.list_completed_verification_evidence(task_id)
            self.assertTrue(evidence)
            self.assertTrue(all(row.provenance is EvidenceProvenance.SYSTEM_PLANNER for row in evidence))
            final = result
        history = await self.client.get(f'/sessions/{session_id}/messages')
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()['task']['status'], final.execution_status)
        evidence = await self.application.sessions.list_completed_verification_evidence(task_id)
        print('S12_FOREGROUND_RESULT ' + json.dumps({'model_rounds': model.calls,
            'generation': state.code_generation, 'execution_status': result.execution_status,
            'verification_status': result.verification_status, 'final_status': final.execution_status,
            'evidence_provenance': [row.provenance.value for row in evidence]}, sort_keys=True))


    async def test_denied_foreground_partial_http_snapshots_render_durable_status(self):
        await self.application.sessions.release_task_execution(self.task.id, 'integration-owner')
        await self.application.sessions.transition_task(self.task.id, TaskStatus.FAILED, 'handcrafted fixture retired')
        model = ProtectedWriteModel()
        self.application.controller._engine._model.model = model
        projects = (await self.client.get('/projects')).json()
        project = next(row for row in projects['projects'] if row['path'] == str(self.root))
        session_id = (await self.client.post('/sessions', json={'project_id':project['id']})).json()['session_id']
        started = await self.client.post(f'/sessions/{session_id}/messages',
            json={'prompt':'Implement the fixed S12 offline fixture write only; no shell or other paths.'})
        self.assertEqual(started.status_code, 200)
        task_id = started.json()['task_id']
        await asyncio.wait_for(self.application.approvals.next_request(), 10)
        cards = (await self.client.get(f'/sessions/{session_id}/requests')).json()
        approval = next(card for card in cards['requests'] if card['kind']=='approval' and card['status']=='pending')
        self.assertEqual((await self.answer(approval, task_id=task_id, approved=False)).status_code, 200)
        await asyncio.wait_for(asyncio.shield(self.host.state.remote_tasks._active.runner), 20)
        self.assertFalse((self.root/'.env').exists())
        self.assertEqual(model.calls, 2)
        before = (await self.client.get(f'/sessions/{session_id}/messages')).json()
        before_status = (await self.client.get('/status')).json()
        pending = (await self.client.get(f'/sessions/{session_id}/requests')).json()
        partial = next(card for card in pending['requests'] if card['kind']=='decision' and card['status']=='pending'
            and card['preview']['operation']=='accept_partial')
        answered = await self.answer(partial, task_id=task_id)
        self.assertEqual(answered.status_code, 200)
        after = (await self.client.get(f'/sessions/{session_id}/messages')).json()
        after_status = (await self.client.get('/status')).json()
        after_requests = (await self.client.get(f'/sessions/{session_id}/requests')).json()
        print('S12_PARTIAL_HTTP_PROBE '+json.dumps({'before_status':before_status.get('status'),
            'before_result':before_status.get('result'), 'after_status':after_status.get('status'),
            'after_result':after_status.get('result'), 'history_task':after['task'],
            'history_session_status':after['session']['status']}))
        self.assertEqual(after['task']['status'], 'accepted_partial')
        self.assertFalse((self.root/'.env').exists())
        for history in (before, after):
            history['messages'] = []
            history['active_prompt'] = None
        snapshot = {'sessionId':session_id,'projects':projects,'before':before,'beforeStatus':before_status,
            'pending':pending,'after':after,'afterStatus':after_status,'afterRequests':after_requests,'response':answered.json()}
        path = self.container/'decision-snapshots.json'
        assert path.is_absolute() and path.resolve().is_relative_to(self.container)
        path.write_text(json.dumps(snapshot), encoding='utf-8')
        script = Path(__file__).resolve().parents[1]/'chaos_agent/remote/tests/decision_snapshot_pwa.js'
        output = await asyncio.to_thread(subprocess.run, ['node',str(script),str(path)],
            capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(output.returncode, 0, output.stdout+output.stderr)
        self.assertEqual(after_status['status'], 'accepted_partial')
        self.assertEqual(after_status['result']['execution_status'], 'accepted_partial')
        self.assertEqual(after_status['result']['verification_status'], 'unverified')
        print('S12_PARTIAL_SNAPSHOT '+json.dumps({'status':after_status['status'],
            'history_task':after['task']['status'],'history_session':after['session']['status'],
            'verification':after_status['result']['verification_status'],'file_exists':False,'model_rounds':model.calls}))


if __name__ == '__main__':
    unittest.main()
