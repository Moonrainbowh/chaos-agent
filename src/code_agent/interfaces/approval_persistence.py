"""Durable binding for the central broker; never dispatches an action."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from dataclasses import asdict, dataclass

from code_agent.core._json import plain
from code_agent.core.action_execution import ActionExecutionContext
from .action_summary import action_activity
from .terminal_display import safe_text


@dataclass
class LiveApproval:
    record: dict
    future: asyncio.Future
    committed: bool = False


class ApprovalPersistence:
    def __init__(self, repository, ttl_seconds, clock):
        self.repository, self.ttl_seconds, self.clock = repository, ttl_seconds, clock
        self.live: dict[str, LiveApproval] = {}
        self.lock = asyncio.Lock()

    async def register(self, request, future, context, workspace_root):
        if self.repository is None or context is None:
            return None
        if not isinstance(context, ActionExecutionContext):
            raise TypeError("execution_context must be ActionExecutionContext")
        if context.task_id is None:
            return None
        if context.request_id != request.request_id:
            raise ValueError("approval action identity mismatch")
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            raise ValueError("approval requires the Host execution workspace")
        binding = await self.repository.approval_binding(context.task_id, workspace_root)
        if binding['thread_id'] != context.owner_thread_id:
            raise ValueError("approval owner thread mismatch")
        material = {
            'action_id': request.request_id, 'name': request.name,
            'arguments': plain(dict(request.arguments)), 'target': request.target,
            'edit_plan': asdict(request.edit_plan) if request.edit_plan else None,
            'context': asdict(context), 'binding': binding,
        }
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        preview = {'summary': safe_text(action_activity(request.name, request.arguments))[:512],
                   'notice': 'Bounded preview; approval covers the bound action digest, not a permanent rule.'}
        if request.edit_plan is not None:
            view = request.edit_plan
            preview['edit_plan'] = {'plan_id': view.plan_id, 'plan_digest': view.plan_digest,
                'risk_flags': list(view.risk_flags), 'path_count': len(view.paths),
                'diff_available_locally': True, 'diff_included': False}
        record = await self.repository.create_approval_request(
            request_id=uuid.uuid4().hex, action_id=request.request_id, action_digest=digest,
            preview=preview, binding=binding, expires_at=self.clock() + self.ttl_seconds)
        live = LiveApproval(record, future)
        self.live[record['request_id']] = live
        return live

    async def consume(self, live, approved):
        record = live.record
        result = await self.repository.consume_approval_request(record['request_id'],
            task_id=record['task_id'], action_digest=record['action_digest'],
            state_version=record['state_version'], owner_instance_id=record['owner_instance_id'],
            approved=approved)
        if result.get('consumed_now') is False and not live.committed:
            raise ValueError("approval was already consumed outside this live waiter")
        live.committed = True
        return result

    async def cleanup(self, live):
        if live is not None:
            self.live.pop(live.record['request_id'], None)
            if not live.committed:
                # List applies durable TTL/owner expiry before narrowly invalidating cancellation.
                await self.repository.list_approval_requests(live.record['task_id'], limit=100)
                await self.repository.invalidate_approval_requests(request_id=live.record['request_id'])

    async def pending(self, task_id):
        if self.repository is None:
            return ()
        return await self.repository.list_approval_requests(task_id, limit=100)

    async def respond(self, request_id, *, task_id, action_digest, state_version,
                      owner_instance_id, approved, authenticate):
        if type(approved) is not bool or not callable(authenticate):
            raise TypeError("response requires a bool and an authentication callback")
        async with self.lock:
            authenticated = authenticate()
            if inspect.isawaitable(authenticated):
                authenticated = await authenticated
            if authenticated is not True:
                raise PermissionError("device authorization is no longer valid")
            live = self.live.get(request_id)
            if live is None:
                raise ValueError("approval no longer has a live execution waiter")
            record = live.record
            supplied = (task_id, action_digest, state_version, owner_instance_id)
            expected = tuple(record[key] for key in (
                'task_id', 'action_digest', 'state_version', 'owner_instance_id'))
            if supplied != expected:
                raise ValueError("approval binding mismatch")
            if live.future.done():
                if not live.committed or live.future.cancelled() or live.future.result() != approved:
                    raise ValueError("approval response is already settled")
                return await self.consume(live, approved)
            result = await self.consume(live, approved)
            if not live.future.done():
                live.future.set_result(approved)
            return result
