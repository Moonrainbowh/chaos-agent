"""Bound mobile answers to the shared approval and persistent task services."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid

from .errors import DeviceAuthorizationError, RemoteConflict, RemoteInputError, RemoteNotFound


class RemoteRequestControl:
    def __init__(self, tasks):
        self.tasks = tasks
        self._lock = asyncio.Lock()

    async def pending(self, session_id):
        async with self._lock:
            return await self._pending(session_id)

    async def _pending(self, session_id):
        task = await self.tasks.request_task(session_id)
        application, task = await self.tasks.request_application(task.id)
        sessions = application.sessions
        records = list(await sessions.list_approval_requests(task.id, limit=100))
        if task.status.value in {'waiting_decision', 'paused', 'interrupted'}:
            facts = await application.foreground_tasks.recovery_checklist(task.id)
            if facts.get('execution_owner') is None:
                binding = await sessions.approval_binding(task.id,
                    task.contract.authorization.workspace_root, kind='decision')
                choices = self._choices(task, facts)
                for preview in choices:
                    digest = self._digest(binding, preview)
                    if any(row['kind'] == 'decision' and row['status'] == 'pending'
                           and row['action_digest'] == digest for row in records):
                        continue
                    card = await sessions.create_approval_request(request_id=uuid.uuid4().hex,
                        action_id='decision:' + preview['operation'], action_digest=digest,
                        preview=preview, binding=binding, expires_at=time.time() + 300)
                    records.append(card)
        return {'task_id': task.id, 'session_id': task.thread_id,
                'requests': sorted(records, key=lambda row: (row['created_at'], row['request_id']), reverse=True)[:100]}

    @staticmethod
    def _digest(binding, preview):
        return hashlib.sha256(json.dumps({'binding': binding, 'preview': preview},
            sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    @staticmethod
    def _choices(task, facts):
        unknown = facts.get('pending_action_records', ())
        choices = []
        if not unknown:
            choices.append({'operation': 'continue', 'summary': 'Continue the same frozen task'})
            if task.status.value == 'waiting_decision':
                choices.append({'operation': 'accept_partial', 'summary': 'Accept partial delivery; no verification is inferred'})
        if task.status.value == 'waiting_decision':
            choices.append({'operation': 'stop', 'summary': 'Stop this task; executed effects remain'})
        for call in unknown[:16]:
            for decision in ('operator_executed', 'operator_not_executed', 'durable_receipt', 'local_mutation'):
                choices.append({'operation': 'reconcile', 'summary': 'Reconcile an unknown action; do not replay',
                    'reconciliation': {'call_id': call['tool_call_id'],
                        'message_sequence': call['message_sequence'], 'version': facts['recovery_version'],
                        'decision': decision}, 'tool_name': str(call['tool_name'])[:128]})
        return choices

    async def respond(self, task_id, body, authenticate):
        if not isinstance(body, dict) or not callable(authenticate):
            raise RemoteInputError('expected a bound response object')
        required = ('request_id', 'action_digest', 'state_version', 'owner_instance_id', 'approved')
        if any(key not in body for key in required) or type(body['approved']) is not bool:
            raise RemoteInputError('missing approval binding or invalid response')
        if set(body).difference((*required, 'reason', 'evidence')):
            raise RemoteInputError('unexpected response fields')
        async with self._lock:
            authenticated = authenticate()
            if inspect.isawaitable(authenticated):
                authenticated = await authenticated
            if authenticated is not True:
                raise DeviceAuthorizationError('device authorization is no longer valid')
            application, task = await self.tasks.request_application(task_id)
            rows = await application.sessions.list_approval_requests(task_id, limit=100)
            card = next((row for row in rows if row['request_id'] == body['request_id']), None)
            if card is None:
                raise RemoteNotFound('unknown request for this task')
            response = {key: body[key] for key in required if key != 'request_id'}
            response['task_id'] = task_id
            if card['kind'] == 'approval':
                return await application.approvals.respond(body['request_id'], **response, authenticate=authenticate)
            operation = card['preview']['operation']
            foreground = application.foreground_tasks
            facts = await foreground.recovery_checklist(task_id)
            if operation in {'continue', 'accept_partial'} and facts.get('unresolved_tool_calls'):
                raise RemoteConflict('unknown actions must be reconciled before this decision')
            prior_result = await foreground.result(task_id) if operation == 'accept_partial' else None
            decision = None
            if operation == 'reconcile' and body['approved']:
                decision = dict(card['preview']['reconciliation'])
                decision.update(reason=self._text(body.get('reason'), 'reason', 2048),
                    evidence=self._text(body.get('evidence'), 'evidence', 8192), operator_authorized=True)
            transition = {'accept_partial': 'accepted_partial', 'stop': 'failed'}.get(operation)
            consumed = await application.sessions.consume_approval_request(body['request_id'],
                **response, decision_transition=transition if body['approved'] else None)
            if not body['approved'] or not consumed['consumed_now']:
                return consumed
            if operation == 'continue':
                consumed['continuation'] = await self.tasks.start('continue safely', task.thread_id)
            elif operation == 'accept_partial':
                await foreground.record_accepted_partial_result(task_id, consumed['task_updated_at'], prior_result)
            elif operation == 'reconcile':
                await foreground.resolve_pending_action(task_id, **decision)
            elif operation != 'stop':
                raise RemoteInputError('unsupported stored decision')
            return consumed

    @staticmethod
    def _text(value, field, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise RemoteInputError(f'{field} must be nonblank bounded text')
        return value
