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
            return tuple({"id": row["id"], **json.loads(row["metadata"])}
                         for row in _rows(connection, thread_id, kind))
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
        """An interrupted/unknown request retains its full reserved liability."""
        if estimate <= 0 or limit <= 0:
            raise ValueError("request reservation must be positive")
        identifier = _id(thread_id, "usage", key)

        def write(connection):
            _require_thread(connection, thread_id)
            rows = _rows(connection, thread_id, "usage")
            records = [json.loads(row["metadata"]) for row in rows]
            if any(row["id"] == identifier for row in rows):
                raise ValueError("request has already been dispatched")
            prior = 0
            task_budget = connection.execute("SELECT input_tokens,output_tokens,max_total_tokens FROM task_budgets WHERE thread_id=?", (thread_id,)).fetchone()
            if not records and task_budget:
                prior = task_budget[0] + task_budget[1]
            spent = sum(r.get("charged", r["reserved"]) + r.get("prior_usage", 0) for r in records) + prior
            first_limit = task_budget[2] if task_budget else limit
            frozen_limit = min(limit, records[0].get("task_limit", limit)) if records else min(limit, first_limit)
            if spent + estimate > frozen_limit:
                raise ValueError("independent task token budget has insufficient remaining capacity")
            _insert(connection, thread_id, "usage", identifier,
                    {"purpose": purpose, "reserved": estimate, "status": "pending", "task_limit": frozen_limit,
                     "prior_usage": prior})
            return identifier
        return await self._database.write(write)

    async def settle_context_call(self, thread_id, identifier, usage, estimated_input):
        def write(connection):
            row = connection.execute(
                "SELECT metadata FROM checkpoints WHERE id=? AND thread_id=? AND label='context:usage'",
                (identifier, thread_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown context request")
            payload = json.loads(row[0])
            update = {"status": "settled", "charged": usage.total_tokens,
                      "usage": usage.to_dict(), "estimated_input": estimated_input}
            if payload["status"] == "settled":
                if any(payload[k] != v for k, v in update.items()):
                    raise ValueError("conflicting request usage")
                return
            payload.update(update)
            connection.execute("UPDATE checkpoints SET metadata=? WHERE id=?",
                               (json.dumps(payload), identifier))
        await self._database.write(write)
