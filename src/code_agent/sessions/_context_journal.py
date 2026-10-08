"""Thread-scoped context journal and conservative request accounting."""
import json
import uuid

from ._codec import encode_datetime, utc_now
from ._records import _require_thread, _thread_maximum


def _rows(connection, thread_id, kind):
    return connection.execute(
        "SELECT id, metadata FROM checkpoints WHERE thread_id=? AND label=? ORDER BY rowid",
        (thread_id, "context:" + kind),
    ).fetchall()


def _insert(connection, thread_id, kind, identifier, payload):
    connection.execute(
        "INSERT INTO checkpoints(id,thread_id,label,metadata,created_at,message_sequence,event_sequence) VALUES(?,?,?,?,?,?,?)",
        (identifier, thread_id, "context:" + kind, json.dumps(payload, ensure_ascii=False),
         encode_datetime(utc_now()), _thread_maximum(connection, "messages", thread_id),
         _thread_maximum(connection, "events", thread_id)),
    )


def _id(thread_id, kind, key):
    return uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([thread_id, kind, key])).hex


class ContextJournalRepositoryMixin:
    async def context_records(self, thread_id, kind):
        def read(connection):
            _require_thread(connection, thread_id)
            from ._shared_budget import binding
            bound = binding(connection, thread_id) if kind == 'usage' else None
            owner = bound['owner_thread_id'] if bound else thread_id
            return tuple({"id": row["id"], **json.loads(row["metadata"])}
                         for row in _rows(connection, owner, kind)
                         if not bound or json.loads(row['metadata']).get('origin_thread_id', owner) == thread_id)
        return await self._database.read(read)

    async def append_context_record(self, thread_id, kind, key, payload, *, expected_tail=...):
        """Append once; window CAS rejects concurrent or stale rotation."""
        if kind not in {"window", "note", "request"}:
            raise ValueError("invalid context record kind")
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 131072:
            raise ValueError("context record is too large")
        identifier = _id(thread_id, kind, key)

        def write(connection):
            _require_thread(connection, thread_id)
            rows = _rows(connection, thread_id, kind)
            for row in rows:
                if row["id"] == identifier:
                    if json.loads(row["metadata"]) != payload:
                        raise ValueError("idempotency key reused with different context data")
                    return identifier
            tail = rows[-1]["id"] if rows else None
            if expected_tail is not ... and tail != expected_tail:
                raise ValueError("context window changed concurrently")
            _insert(connection, thread_id, kind, identifier, payload)
            return identifier
        return await self._database.write(write)

    async def reserve_context_call(self, thread_id, key, estimate, limit, purpose):
        """Reserve a request against the root owner and any frozen child ceiling."""
        from ._shared_budget import binding, require_active_parent, token_spent
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (estimate, limit)):
            raise ValueError("request reservation must be positive")
        identifier = _id(thread_id, "usage", key)

        def write(connection):
            _require_thread(connection, thread_id)
            bound = binding(connection, thread_id)
            owner = bound['owner_thread_id'] if bound else thread_id
            if bound:
                require_active_parent(connection, bound)
            rows = _rows(connection, owner, "usage")
            records = [json.loads(row['metadata']) for row in rows]
            if any(row['id'] == identifier for row in rows):
                raise ValueError("request has already been dispatched")
            budget = connection.execute('SELECT max_total_tokens FROM task_budgets WHERE thread_id=?', (owner,)).fetchone()
            frozen = budget[0] if bound and budget else min(limit, budget[0] if budget else limit)
            if records:
                frozen = min(frozen, records[0].get('task_limit', frozen))
            if token_spent(connection, owner) + estimate > frozen:
                raise ValueError("shared task token budget has insufficient remaining capacity")
            if bound and token_spent(connection, owner, thread_id) + estimate > min(limit, bound['max_total_tokens']):
                raise ValueError("child task token budget has insufficient remaining capacity")
            _insert(connection, owner, "usage", identifier,
                    dict(purpose=purpose, reserved=estimate, status='pending', task_limit=frozen,
                         origin_thread_id=thread_id, budget_owner_thread_id=owner, projected_tokens=0))
            return identifier
        return await self._database.write(write)

    async def settle_context_call(self, thread_id, identifier, usage, estimated_input, *, completed=True):
        """Persist cumulative known usage once; partial retains unknown liability."""
        from code_agent.core.models import Usage
        from ._shared_budget import binding
        from ._task_budget import task_budget, sync_lineage_usage
        if not isinstance(usage, Usage) or not isinstance(completed, bool):
            raise TypeError('usage and completed have invalid types')
        if isinstance(estimated_input, bool) or not isinstance(estimated_input, int) or estimated_input < 0:
            raise ValueError('estimated_input must be non-negative')

        def write(connection):
            bound = binding(connection, thread_id)
            owner = bound['owner_thread_id'] if bound else thread_id
            row = connection.execute(
                "SELECT metadata FROM checkpoints WHERE id=? AND thread_id=? AND label='context:usage'",
                (identifier, owner)).fetchone()
            if row is None:
                raise ValueError('unknown context request')
            payload = json.loads(row[0])
            if payload.get('origin_thread_id', owner) != thread_id:
                raise ValueError('context request belongs to another origin')
            update = dict(status='settled' if completed else 'partial', charged=usage.total_tokens,
                          usage=usage.to_dict(), estimated_input=estimated_input)
            if payload['status'] == 'settled':
                if any(payload[k] != v for k, v in update.items()):
                    raise ValueError('conflicting request usage')
                return
            # Missing or zero usage is not evidence that the request cost zero.
            if usage.total_tokens == 0:
                return
            previous = payload.get('usage', {})
            if any(usage.to_dict().get(k, 0) < previous.get(k, 0) for k in ('input_tokens', 'output_tokens')):
                raise ValueError('conflicting decreasing request usage')
            delta_input = usage.input_tokens - previous.get('input_tokens', 0)
            delta_output = usage.output_tokens - previous.get('output_tokens', 0)
            for target in dict.fromkeys((owner, thread_id)):
                budget = connection.execute('SELECT * FROM task_budgets WHERE thread_id=?', (target,)).fetchone()
                if budget is not None:
                    connection.execute('UPDATE task_budgets SET input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE thread_id=?',
                                       (delta_input, delta_output, target))
                    updated = connection.execute('SELECT * FROM task_budgets WHERE thread_id=?', (target,)).fetchone()
                    sync_lineage_usage(connection, target, task_budget(updated))
            payload.update(update)
            payload['projected_tokens'] = payload.get('projected_tokens', 0) + delta_input + delta_output
            connection.execute('UPDATE checkpoints SET metadata=? WHERE id=?', (json.dumps(payload), identifier))
        await self._database.write(write)
