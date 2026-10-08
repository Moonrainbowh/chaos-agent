"""Transactional reconciliation of a specific unresolved durable action.

This module never executes an action. Operator reports remain explicitly unverified.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace

from code_agent.core._json import plain
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message
from code_agent.core.pending_actions import pending_calls
from code_agent.core.task_state import TaskState

from ._codec import decode_event, decode_message, decode_task_state, encode_task_state, encode_datetime, encode_event, encode_message, utc_now
from ._records import _touch_thread
from ._task_records import _task_row, row_task
from .errors import SessionNotFound


def recovery_snapshot(connection: sqlite3.Connection, task_id: str):
    row = _task_row(connection, "id", task_id)
    if row is None:
        raise SessionNotFound("task not found")
    task = row_task(row)
    from ._history_queries import history_stats, pending_rows
    from ._history_schema import validate_history_triggers
    validate_history_triggers(connection)
    stats = history_stats(connection, task.thread_id)
    pending = pending_rows(connection, task.thread_id)
    # Trigger revisions cover same-sequence edits/deletes as well as appends.
    # Budget admissions are deliberately excluded, as in the original material.
    state_rows = connection.execute("SELECT payload,updated_at FROM task_states WHERE thread_id=?", (task.thread_id,)).fetchall()
    owner_rows = connection.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchall()
    material = [task.to_dict(), stats, [tuple(row) for row in state_rows], [tuple(row) for row in owner_rows]]
    version = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    records = tuple({"tool_call_id": item["id"], "tool_name": item["name"],
        "message_sequence": item["sequence"],
        "arguments_sha256":hashlib.sha256(json.dumps(json.loads(item["arguments"]),sort_keys=True).encode()).hexdigest()} for item in pending)
    rows, messages, events = [], (), []
    if pending:
        sequences = tuple(dict.fromkeys(item["sequence"] for item in pending))
        marks = ",".join("?" for _ in sequences)
        headers = connection.execute(f"SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM messages WHERE thread_id=? AND sequence IN ({marks})", (task.thread_id,*sequences)).fetchall()
        if sum(item["size"] for item in headers)>1048576:
            raise ValueError("pending action messages exceed bounded recovery capacity")
        rows = connection.execute(f"SELECT sequence,payload,created_at FROM messages WHERE thread_id=? AND sequence IN ({marks}) ORDER BY sequence", (task.thread_id,*sequences)).fetchall()
        messages = tuple(decode_message(item["payload"]) for item in rows)
        identifiers = tuple(dict.fromkeys(item["id"] for item in pending))
        marks = ",".join("?" for _ in identifiers)
        where = f"thread_id=? AND ((json_extract(payload,'$.kind')='message_added' AND EXISTS (SELECT 1 FROM json_each(payload,'$.payload.message.tool_calls') j WHERE json_extract(j.value,'$.id') IN ({marks}))) OR (json_extract(payload,'$.kind')='action_requested' AND json_extract(payload,'$.payload.request.id') IN ({marks})) OR (json_extract(payload,'$.kind')='action_completed' AND json_extract(payload,'$.payload.result.request_id') IN ({marks})))"
        parameters = (task.thread_id,*identifiers,*identifiers,*identifiers)
        headers = connection.execute(f"SELECT sequence,length(CAST(payload AS BLOB)) AS size FROM events WHERE {where} ORDER BY sequence LIMIT 1001",parameters).fetchall()
        if len(headers)>1000 or sum(item['size'] for item in headers)>1048576:
            raise ValueError("pending action receipts exceed bounded recovery capacity")
        events = connection.execute(f"SELECT sequence,payload,created_at FROM events WHERE {where} ORDER BY sequence LIMIT 1000",parameters).fetchall()
    return task, rows, messages, events, version, records


def mutation_receipt(connection: sqlite3.Connection, task_id: str, call_id: str):
    from ._rewind_rows import mutation_record
    row = _task_row(connection, "id", task_id)
    if row is None:
        raise SessionNotFound("task not found")
    rows = connection.execute("SELECT * FROM workspace_mutations WHERE task_id=? AND origin_thread_id=? AND request_id=?", (task_id, row["thread_id"], call_id)).fetchall()
    if len(rows) != 1 or rows[0]["status"] != "completed":
        raise ValueError("no unique completed workspace mutation receipt")
    paths = connection.execute("SELECT * FROM workspace_mutation_paths WHERE mutation_sequence=? ORDER BY ordinal", (rows[0]["sequence"],)).fetchall()
    return mutation_record(rows[0], paths)


def _receipt(events, call, assistant: Message) -> tuple[ActionResult, int]:
    requested = []
    completed = []
    expected = {"id": call.id, "name": call.name, "arguments": plain(call.arguments)}
    anchors = [row["sequence"] for row in events
               if (event := decode_event(row["payload"])).kind is EventKind.MESSAGE_ADDED
               and plain(event.payload.get("message")) == assistant.to_dict()]
    if len(anchors) != 1:
        raise ValueError("no unique original assistant event; outcome remains unresolved")
    for row in events:
        event = decode_event(row["payload"])
        if event.payload.get("origin") is not None or row["sequence"] <= anchors[0]:
            continue
        if event.kind is EventKind.ACTION_REQUESTED and plain(event.payload.get("request")) == expected:
            requested.append(row["sequence"])
        if event.kind is EventKind.ACTION_COMPLETED:
            data = event.payload.get("result")
            if not isinstance(data, dict) and not hasattr(data, "keys"):
                continue
            result = ActionResult.from_dict(data)
            if result.request_id == call.id and result.name == call.name:
                completed.append((result, row["sequence"]))
    if len(requested) != 1 or len(completed) != 1 or completed[0][1] <= requested[0]:
        raise ValueError("no unique trusted durable receipt; outcome remains unresolved")
    return completed[0]


def resolve_action(connection: sqlite3.Connection, *, task_id: str, call_id: str,
                   message_sequence: int, version: str, workspace_root: str,
                   decision: str, reason: str, evidence: str,
                   workspace_fingerprint: str | None = None, owner_alive=None) -> AgentEvent:
    task, rows, messages, events, current, records = recovery_snapshot(connection, task_id)
    if current != version:
        raise ValueError("stale recovery version")
    if task.status.is_terminal or task.status.value in {"running", "verifying"}:
        raise ValueError("task must be stopped before reconciliation")
    if os.path.normcase(os.path.realpath(workspace_root)) != os.path.normcase(os.path.realpath(task.contract.authorization.workspace_root)):
        raise ValueError("recovery workspace does not match frozen task authorization")
    matching = [record for record in records if record["tool_call_id"] == call_id and record["message_sequence"] == message_sequence]
    occurrences = [(row, message, call) for row, message in zip(rows, messages) for call in message.tool_calls if call.id == call_id]
    if len(matching) != 1 or len(occurrences) != 1:
        raise ValueError("action is not uniquely pending in this task")
    row, assistant, call = occurrences[0]
    receipt_sequence = None
    changed_paths = ()
    if decision == "durable_receipt":
        result, receipt_sequence = _receipt(events, call, assistant)
    elif decision == "local_mutation":
        receipt = mutation_receipt(connection, task_id, call_id)
        if workspace_fingerprint is None or receipt.coverage.workspace_fingerprint != workspace_fingerprint:
            raise ValueError("workspace mutation identity was not verified by Host")
        result = ActionResult(call.id, call.name,
            {"recovered": True, "paths": [path.path for path in receipt.paths]},
            metadata={"outcome_source": "verified_local_mutation", "mutation_id": receipt.mutation_id})
        receipt_sequence = receipt.sequence
        changed_paths = tuple(path.path for path in receipt.paths)
    elif decision in {"operator_executed", "operator_not_executed"}:
        result = ActionResult(call.id, call.name,
            {"recovery_decision": decision, "verified": False,
             "notice": "Explicit operator report; original action must not be replayed. No tool success or verification is inferred."},
            is_error=True, metadata={"outcome_source": "operator_report"})
    else:
        raise ValueError("unsupported recovery decision")
    owner = connection.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
    if owner is not None:
        if not callable(owner_alive) or owner_alive(owner['owner_pid'], owner['owner_create_time']):
            raise ValueError("execution owner has not released the action; wait for it to stop")
        # Only the verified dead owner in this same snapshot can be retired.
        connection.execute("DELETE FROM task_executions WHERE task_id=? AND instance_id=?", (task_id, owner['instance_id']))
    message = Message("tool", content=json.dumps(result.to_dict(), ensure_ascii=False), name=call.name, tool_call_id=call.id)
    event = AgentEvent(EventKind.ACTION_OUTCOME_RESOLVED, {
        "task_id": task.id, "thread_id": task.thread_id, "tool_call_id": call.id,
        "message_sequence": message_sequence, "recovery_version": version,
        "decision": decision, "reason": reason, "evidence": evidence,
        "receipt_sequence": receipt_sequence, "workspace_fingerprint": workspace_fingerprint,
        "verified": decision in {"durable_receipt", "local_mutation"}})
    from .conversation_tree import _ensure_nodes
    from ._conversation_schema import record_message_node
    _ensure_nodes(connection, task.thread_id)
    timestamp = encode_datetime(utc_now())
    if decision != "durable_receipt":
        # A crash can occur after a workspace write but before Core records its generation.
        # Manual reports also cannot preserve a formerly verified subject as current proof.
        state_row = connection.execute("SELECT payload FROM task_states WHERE thread_id=?", (task.thread_id,)).fetchone()
        state = TaskState.empty() if state_row is None else decode_task_state(state_row["payload"])
        state = replace(state, code_generation=state.code_generation + 1, subject_hash="",
            files_changed=tuple(dict.fromkeys((*state.files_changed, *changed_paths)))[-32:],
            verified_facts=(), working_notes=(*state.working_notes, "Recovery changed or could have changed the subject; prior verification is historical.")[-32:])
        connection.execute("INSERT INTO task_states(thread_id,payload,updated_at) VALUES (?,?,?) ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (task.thread_id, encode_task_state(state), timestamp))
    cursor = connection.execute("INSERT INTO messages(thread_id,payload,created_at) VALUES (?,?,?)", (task.thread_id, encode_message(message), timestamp))
    record_message_node(connection, task.thread_id, cursor.lastrowid)
    connection.execute("INSERT INTO events(thread_id,payload,created_at) VALUES (?,?,?)", (task.thread_id, encode_event(event), timestamp))
    _touch_thread(connection, task.thread_id, timestamp)
    return event
