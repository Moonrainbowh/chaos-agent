"""Project actual child mutations, never child claims, into the parent ledger."""
import hashlib
import json
import uuid
from dataclasses import replace
from collections.abc import Mapping

from code_agent.core._json import plain
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task_state import TaskState, reduce_task_state
from ._codec import decode_task_state, encode_task_state
from ._records import _thread_maximum, _touch_thread
from ._shared_budget import binding


def _mutates(request, result):
    if request.name in {'write_file', 'replace_text'}:
        return not result.is_error or result.metadata.get('workspace_may_have_changed') is True
    if request.name == 'apply_workspace_edit_plan_v1':
        return isinstance(result.output, Mapping) and result.output.get('workspace_may_have_changed') is True
    return request.name in {'run_command', 'run_process_v1'} and result.metadata.get('execution_attempted') is True


def project_child_action(connection, child, request, result, timestamp):
    payload = binding(connection, child)
    if payload is None or not _mutates(request, result):
        return
    owner = payload['owner_thread_id']
    row = connection.execute(
        'SELECT t.thread_id,e.instance_id FROM tasks t LEFT JOIN task_executions e ON e.task_id=t.id WHERE t.id=?',
        (payload['parent_task_id'],)).fetchone()
    if row is None or row[0] != owner or row[1] != payload['owner_instance_id']:
        raise ValueError('child mutation belongs to an earlier parent execution')
    identity = uuid.uuid5(uuid.NAMESPACE_URL, 'child-action:' + child + ':' + request.id).hex
    fingerprint = hashlib.sha256(json.dumps(dict(request=request.to_dict(),
        result=dict(output=plain(result.output), is_error=result.is_error, metadata=plain(result.metadata))),
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    previous = connection.execute('SELECT metadata FROM checkpoints WHERE id=?', (identity,)).fetchone()
    if previous:
        if json.loads(previous[0])['fingerprint'] != fingerprint:
            raise ValueError('child action replay changed its result')
        return
    row = connection.execute('SELECT payload FROM task_states WHERE thread_id=?', (owner,)).fetchone()
    current = TaskState.empty() if row is None else decode_task_state(row[0])
    effect_request, effect_result = request, result
    if request.name in {'write_file', 'replace_text'} and result.is_error:
        path = request.arguments.get('path')
        effect_request = ActionRequest(request.id, 'apply_workspace_edit_plan_v1', {})
        effect_result = ActionResult(request.id, effect_request.name,
            {'workspace_may_have_changed': True, 'paths': [path] if isinstance(path, str) else []}, is_error=True)
    updated = replace(reduce_task_state(current, effect_request, effect_result),
                      code_generation=current.code_generation + 1, subject_hash='')
    connection.execute('INSERT INTO task_states(thread_id,payload,updated_at) VALUES(?,?,?) '
        'ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
        (owner, encode_task_state(updated), timestamp))
    connection.execute('INSERT INTO checkpoints(id,thread_id,label,metadata,created_at,message_sequence,event_sequence) '
        'VALUES(?,?,?,?,?,?,?)', (identity, child, 'context:child_action',
        json.dumps(dict(fingerprint=fingerprint, parent_task_id=payload['parent_task_id'], generation=updated.code_generation)),
        timestamp, _thread_maximum(connection, 'messages', child), _thread_maximum(connection, 'events', child)))
    _touch_thread(connection, owner, timestamp)


def reject_stale_parent_state(connection, thread_id, state):
    """An outside-transaction subject snapshot cannot replace a child mutation."""
    receipts = connection.execute("SELECT c.metadata FROM checkpoints c JOIN threads t ON t.id=c.thread_id "
        "WHERE t.parent_thread_id=? AND c.label='context:child_action'", (thread_id,)).fetchall()
    task = connection.execute('SELECT id FROM tasks WHERE thread_id=? ORDER BY created_at DESC LIMIT 1', (thread_id,)).fetchone()
    generations = [payload['generation'] for row in receipts
        if (payload := json.loads(row[0]))['parent_task_id'] == (task[0] if task else None)]
    if not generations:
        return
    row = connection.execute('SELECT payload FROM task_states WHERE thread_id=?', (thread_id,)).fetchone()
    if row is None:
        return
    current = decode_task_state(row[0])
    if state.code_generation < max(generations) or (
        state.code_generation == current.code_generation
        and not set(current.files_changed).issubset(state.files_changed)
    ):
        raise ValueError('task state snapshot predates a child mutation')
