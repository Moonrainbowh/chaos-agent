"""Bounded journal queries; original payloads and sequence identities are retained."""
from __future__ import annotations
import hashlib
import json
import uuid
from ._codec import decode_message, decode_event, decode_datetime
from ._records import _require_thread, _text
from .models import MessageRecord


def bounded(value, maximum, name, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= maximum:
        raise ValueError(f"{name} outside bounded range")
    return value


def history_stats(connection, thread):
    _require_thread(connection, thread)
    messages = connection.execute("SELECT COUNT(*),COALESCE(MAX(sequence),0),COALESCE(MIN(sequence),0) FROM messages WHERE thread_id=?", (thread,)).fetchone()
    events = connection.execute("SELECT COUNT(*),COALESCE(MAX(sequence),0) FROM events WHERE thread_id=?", (thread,)).fetchone()
    revision = connection.execute("SELECT * FROM history_revisions WHERE thread_id=?", (thread,)).fetchone()
    return {"message_count": messages[0], "message_sequence": messages[1], "message_first_sequence":messages[2],
        "event_count": events[0], "event_sequence": events[1],
        **({key: revision[key] for key in revision.keys() if key != "thread_id"} if revision else {
            "message_revision": 0, "event_revision": 0, "message_epoch": 0,
            "mutation_revision": 0, "state_revision": 0, "owner_revision": 0})}


def read_page(connection, thread, *, after_sequence=0, before_sequence=None,
              limit=100, max_bytes=1048576, role=None, newest=False):
    bounded(after_sequence, 2**63-1, "after_sequence", zero=True)
    if before_sequence is not None:
        bounded(before_sequence, 2**63-1, "before_sequence", zero=True)
    bounded(limit, 1000, "limit")
    bounded(max_bytes, 16*1024*1024, "max_bytes")
    if role not in {None, "user", "assistant", "tool", "system", "developer"}:
        raise ValueError("invalid history role")
    _require_thread(connection, thread)
    where, args = "thread_id=? AND sequence>?", [thread, after_sequence]
    if before_sequence is not None:
        where += " AND sequence<?"
        args.append(before_sequence)
    if role:
        where += " AND json_extract(payload,'$.role')=?"
        args.append(role)
    order = "DESC" if newest else "ASC"
    headers = connection.execute(f"SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM messages WHERE {where} ORDER BY sequence {order} LIMIT ?", (*args, limit)).fetchall()
    selected, size = [], 0
    for header in headers:
        if size + header["size"] > max_bytes:
            if not selected:
                raise ValueError("required history message exceeds byte budget")
            break
        size += header["size"]
        selected.append(header["sequence"])
    if not selected:
        return ()
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(f"SELECT sequence,payload,created_at FROM messages WHERE thread_id=? AND sequence IN ({placeholders}) ORDER BY sequence", (thread, *selected)).fetchall()
    return tuple(MessageRecord(row["sequence"], thread, decode_message(row["payload"]),
        decode_datetime(row["created_at"], "message")) for row in rows)


def stable_item_id(thread, sequence):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"chaos:item:{thread}:{sequence}").hex


def ensure_item_ids(connection, thread):
    """Legacy UUID lookup backfill reads only bounded sequence metadata, never payloads."""
    while True:
        rows = connection.execute("SELECT m.sequence FROM messages m LEFT JOIN history_item_ids h ON h.thread_id=m.thread_id AND h.sequence=m.sequence WHERE m.thread_id=? AND h.sequence IS NULL ORDER BY m.sequence LIMIT 1000", (thread,)).fetchall()
        if not rows:
            return
        connection.executemany("INSERT INTO history_item_ids(thread_id,sequence,item_id) VALUES (?,?,?)",
            ((thread, row[0], stable_item_id(thread, row[0])) for row in rows))


def pending_rows(connection, thread, limit=256):
    """Duplicate IDs stay pending; only a later exact name+ID result pairs a call."""
    bounded(limit, 1000, "limit")
    _require_thread(connection, thread)
    rows = connection.execute("""WITH counts AS (SELECT call_id,COUNT(*) AS n FROM history_calls WHERE thread_id=? GROUP BY call_id)
        SELECT c.sequence,c.ordinal,length(CAST(c.arguments AS BLOB)) AS size,c.call_id AS id,c.name FROM history_calls c JOIN counts n ON n.call_id=c.call_id
        WHERE c.thread_id=? AND c.role='assistant' AND (n.n<>1 OR NOT EXISTS (SELECT 1 FROM history_results r WHERE r.thread_id=?
          AND r.sequence>c.sequence AND r.call_id=c.call_id AND r.name=c.name))
        ORDER BY c.sequence,c.ordinal LIMIT ?""", (thread,thread,thread,limit+1)).fetchall()
    if len(rows) > limit:
        raise ValueError("pending action set exceeds bounded recovery capacity")
    if sum(row['size'] for row in rows)>1048576:
        raise ValueError("pending action arguments exceed bounded recovery byte capacity")
    return tuple({"sequence":row['sequence'],"id":row['id'],"name":row['name'],
        "arguments":connection.execute("SELECT arguments FROM history_calls WHERE thread_id=? AND sequence=? AND ordinal=?",(thread,row['sequence'],row['ordinal'])).fetchone()[0]} for row in rows)


class HistoryQueryRepositoryMixin:
    async def history_stats(self, thread_id):
        thread_id = _text(thread_id, "thread_id")
        return await self._database.read(lambda c: history_stats(c, thread_id))

    async def read_history_page(self, thread_id, **kwargs):
        thread_id = _text(thread_id, "thread_id")
        return await self._database.read(lambda c: read_page(c, thread_id, **kwargs))

    async def read_history_item(self, thread_id, *, sequence=None, item_id=None, max_bytes=1048576):
        if (sequence is None) == (item_id is None):
            raise ValueError("supply one history identity")
        def read(c):
            _require_thread(c, thread_id)
            selected = sequence
            if item_id is not None:
                row = c.execute("SELECT sequence FROM history_item_ids WHERE thread_id=? AND item_id=?", (thread_id, item_id)).fetchone()
                if row is None:
                    ensure_item_ids(c, thread_id)
                    row = c.execute("SELECT sequence FROM history_item_ids WHERE thread_id=? AND item_id=?", (thread_id,item_id)).fetchone()
                selected = row[0] if row else None
            if selected is None:
                return None
            bounded(selected, 2**63-1, "sequence")
            records = read_page(c, thread_id, after_sequence=selected-1, before_sequence=selected+1, limit=1, max_bytes=max_bytes)
            return records[0] if records else None
        return await self._database.write(read)

    async def pending_action_records(self, thread_id, *, limit=256):
        def read(c):
            rows = pending_rows(c, thread_id, limit)
            return tuple({"tool_call_id": row["id"], "tool_name": row["name"],
                "message_sequence": row["sequence"], "arguments": json.loads(row["arguments"]),
                "arguments_sha256": hashlib.sha256(json.dumps(json.loads(row["arguments"]), sort_keys=True).encode()).hexdigest()} for row in rows)
        return await self._database.read(read)

    async def has_tool_call_id(self, thread_id, call_id):
        def read(c):
            _require_thread(c, thread_id)
            return c.execute("SELECT 1 FROM history_calls WHERE thread_id=? AND call_id=? LIMIT 1", (thread_id, call_id)).fetchone() is not None
        return await self._database.read(read)

    async def load_host_progress_projection(self, thread_id):
        def read(c):
            _require_thread(c, thread_id)
            row = c.execute("SELECT cursor,epoch,length(CAST(payload AS BLOB)) FROM history_progress WHERE thread_id=?", (thread_id,)).fetchone()
            if row is None:
                return None
            bounded(row[0],2**63-1,"progress cursor",zero=True)
            bounded(row[1],2**63-1,"progress epoch",zero=True)
            if row[2]>16*1024*1024:
                raise ValueError("progress projection exceeds bounded byte capacity")
            text=c.execute("SELECT payload FROM history_progress WHERE thread_id=?",(thread_id,)).fetchone()[0]
            state=json.loads(text)
            if not isinstance(state,dict):
                raise ValueError("invalid progress projection")
            return {"cursor": row[0], "epoch": row[1], "state":state}
        return await self._database.read(read)

    async def save_host_progress_projection(self, thread_id, payload, *, expected_cursor, expected_epoch):
        bounded(expected_cursor,2**63-1,"expected cursor",zero=True)
        bounded(expected_epoch,2**63-1,"expected epoch",zero=True)
        bounded(payload['cursor'],2**63-1,"progress cursor",zero=True)
        def write(c):
            stats = history_stats(c, thread_id)
            row = c.execute("SELECT cursor,epoch FROM history_progress WHERE thread_id=?", (thread_id,)).fetchone()
            cursor = row[0] if row and row[1] == expected_epoch else 0
            if stats["message_epoch"] != expected_epoch or cursor != expected_cursor:
                return False
            text = json.dumps(payload["state"], ensure_ascii=False)
            if len(text.encode()) > 16*1024*1024 or not expected_cursor<=payload["cursor"]<=stats["message_sequence"]:
                raise ValueError("invalid progress projection size/cursor")
            c.execute("INSERT INTO history_progress VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET cursor=excluded.cursor,epoch=excluded.epoch,payload=excluded.payload", (thread_id, payload["cursor"], expected_epoch, text))
            return True
        return await self._database.write(write)

    async def load_context_messages(self, thread_id, *, limit=100, max_bytes=1048576):
        def read(c):
            records = list(read_page(c, thread_id, newest=True, limit=limit, max_bytes=max_bytes))
            # A tail starting in a tool group must include its original assistant.
            if records:
                tool_ids = {r.message.tool_call_id for r in records if r.message.role == "tool"}
                covered = {call.id for r in records for call in r.message.tool_calls}
                missing = tool_ids-covered
                if missing:
                    marks = ",".join("?" for _ in missing)
                    first = c.execute(f"SELECT MIN(sequence) FROM history_calls WHERE thread_id=? AND call_id IN ({marks})", (thread_id,*missing)).fetchone()[0]
                    if first is not None:
                        records = list(read_page(c, thread_id, after_sequence=first-1, limit=1000, max_bytes=max_bytes))
                        if records and records[-1].sequence != history_stats(c, thread_id)["message_sequence"]:
                            raise ValueError("required tool group exceeds context history budget")
            user = read_page(c, thread_id, role="user", newest=True, limit=1, max_bytes=max_bytes)
            if user and user[0].sequence not in {r.sequence for r in records}:
                records.insert(0, user[0])
            if sum(len(json.dumps(r.message.to_dict(), ensure_ascii=False).encode()) for r in records) > max_bytes:
                raise ValueError("required user/tool context exceeds byte budget")
            return tuple(r.message for r in records)
        return await self._database.read(read)

    async def load_latest_task_events(self, task_id, generation, subject_hash, *, limit=32):
        bounded(limit, 1000, "limit")
        def read(c):
            task = c.execute("SELECT thread_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task not found")
            rows = list(c.execute("""SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM events WHERE thread_id=?
                AND json_extract(payload,'$.payload.task_id')=? AND json_extract(payload,'$.payload.result_generation')=?
                AND json_extract(payload,'$.payload.result_subject_hash') IS ? AND json_type(payload,'$.payload.result')='object'
                ORDER BY sequence DESC LIMIT ?""", (task[0],task_id,generation,subject_hash,limit)).fetchall())
            running = c.execute("""SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM events WHERE thread_id=?
                AND json_extract(payload,'$.kind')='task_status_changed' AND json_extract(payload,'$.payload.task_id')=?
                AND json_extract(payload,'$.payload.status')='running' ORDER BY sequence DESC LIMIT 1""",(task[0],task_id)).fetchone()
            if running is not None and running['sequence'] not in {row['sequence'] for row in rows}:
                rows.append(running)
            if sum(row["size"] for row in rows)>1048576:
                raise ValueError("task result events exceed byte budget")
            if not rows:
                return ()
            marks=",".join("?" for _ in rows)
            events=c.execute(f"SELECT payload FROM events WHERE thread_id=? AND sequence IN ({marks}) ORDER BY sequence", (task[0],*(r["sequence"] for r in rows))).fetchall()
            return tuple(decode_event(r[0]) for r in events)
        return await self._database.read(read)
