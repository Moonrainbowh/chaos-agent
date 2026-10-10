"""Persistent child bindings and shared request admission, using checkpoints."""
import json

from ._codec import encode_datetime, utc_now
from ._records import _require_thread, _text, _thread_maximum


def binding(connection, thread_id):
    rows = connection.execute(
        "SELECT metadata FROM checkpoints WHERE thread_id=? AND label='context:child_budget'",
        (thread_id,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("ambiguous child budget binding")
    return json.loads(rows[0][0]) if rows else None


def require_active_parent(connection, payload):
    row = connection.execute(
        "SELECT t.thread_id,t.status,e.instance_id FROM tasks t LEFT JOIN task_executions e "
        "ON e.task_id=t.id WHERE t.id=?", (payload['parent_task_id'],),
    ).fetchone()
    if row is None or row[0] != payload['owner_thread_id'] or row[1] not in {'running', 'verifying'} or row[2] is None:
        raise ValueError("child budget parent is not actively owned")
    if payload.get('owner_instance_id', row[2]) != row[2]:
        raise ValueError('child budget belongs to an earlier parent execution')
    return row[2]


def usage_records(connection, owner):
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT metadata FROM checkpoints WHERE thread_id=? AND label='context:usage'", (owner,))]


def liability(record):
    if record.get('status') == 'settled':
        return record.get('charged', record['reserved'])
    return max(record['reserved'], record.get('charged', 0))


def token_spent(connection, owner, origin=None):
    records = usage_records(connection, owner)
    selected = records if origin is None else [r for r in records if r.get('origin_thread_id', owner) == origin]
    row = connection.execute('SELECT input_tokens,output_tokens FROM task_budgets WHERE thread_id=?',
                             (origin or owner,)).fetchone()
    # Journal actual is projected into task_budgets; subtract it once before
    # adding conservative pending liabilities and legacy actual consumption.
    actual = (row[0] + row[1]) if row else 0
    projected = sum(r.get('projected_tokens', r.get('charged', 0)) for r in selected)
    legacy_prior = sum(r.get('prior_usage', 0) for r in selected)
    return max(actual - projected, legacy_prior) + sum(liability(r) for r in selected)


async def bind_child(database, child, owner, task, request, *, max_total_tokens,
                     max_tool_calls, max_agent_rounds=100, max_children=8, required_sources=()):
    from code_agent.core.source_completion import freeze_sources
    required_sources = freeze_sources(required_sources)
    for name, value in [('child_thread_id', child), ('owner_thread_id', owner),
                        ('parent_task_id', task), ('delegate_request_id', request)]:
        _text(value, name)
    for value in (max_total_tokens, max_agent_rounds, max_children):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('child budget limits must be positive integers')
    if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int) or max_tool_calls < 0:
        raise ValueError('child tool budget must be non-negative')
    payload = dict(owner_thread_id=owner, parent_task_id=task, delegate_request_id=request,
                   max_total_tokens=max_total_tokens, max_tool_calls=max_tool_calls,
                   max_agent_rounds=max_agent_rounds, max_children=max_children,
                   required_sources=list(required_sources))

    def write(connection):
        _require_thread(connection, child)
        payload['owner_instance_id'] = require_active_parent(connection, payload)
        relation = connection.execute('SELECT parent_thread_id FROM threads WHERE id=?', (child,)).fetchone()
        if child == owner or relation[0] != owner:
            raise ValueError('child thread is outside parent tree')
        existing = binding(connection, child)
        if existing is not None:
            existing = {**existing, 'required_sources': existing.get('required_sources', [])}
            if existing != payload:
                raise ValueError('child budget identity or limits changed')
            return existing
        siblings = [json.loads(row[0]) for row in connection.execute(
            "SELECT c.metadata FROM checkpoints c JOIN threads t ON t.id=c.thread_id "
            "WHERE t.parent_thread_id=? AND c.label='context:child_budget'", (owner,))]
        if any(p['delegate_request_id'] == request for p in siblings):
            raise ValueError('delegation request has already created a child')
        frozen_children = min([max_children] + [p.get('max_children', 8) for p in siblings])
        if len(siblings) >= frozen_children:
            raise ValueError('parent child-start budget exhausted')
        owner_budget = connection.execute('SELECT 1 FROM task_budgets WHERE thread_id=?', (owner,)).fetchone()
        if owner_budget is None:
            raise ValueError('parent task budget is missing')
        import uuid
        connection.execute(
            'INSERT INTO checkpoints(id,thread_id,label,metadata,created_at,message_sequence,event_sequence) VALUES(?,?,?,?,?,?,?)',
            (uuid.uuid5(uuid.NAMESPACE_URL, 'child-budget:' + child).hex, child,
             'context:child_budget', json.dumps(payload), encode_datetime(utc_now()),
             _thread_maximum(connection, 'messages', child), _thread_maximum(connection, 'events', child)))
        return payload
    return await database.write(write)
