from __future__ import annotations

import sqlite3

from ._records import _text


class RecoveryRepositoryMixin:
    """Build a bounded, read-only recovery fact sheet from durable records."""

    async def recovery_checklist(self, task_id: str) -> dict[str, object]:
        task_id = _text(task_id, "task_id")
        task = await self.load_task(task_id)
        checkpoints = await self.list_checkpoints(task.thread_id)
        notes = await self.context_note_files(task.thread_id)
        followups = await self.list_task_followups(task_id)
        messages = await self.load_message_records(task.thread_id)
        events = await self.load_events(task.thread_id)
        latest_message = max((int(getattr(item, "sequence", 0)) for item in messages), default=0)
        latest_event = max((int(getattr(item, "sequence", 0)) for item in events), default=0)
        covered_message = max((int(n.get("coverage", {}).get("message_sequence", 0)) for n in notes), default=0)
        covered_event = max((int(n.get("coverage", {}).get("event_sequence", 0)) for n in notes), default=0)
        owner = await self._database.read(lambda connection: _execution_owner(connection, task_id))  # type: ignore[attr-defined]
        return {
            "task_id": task.id, "thread_id": task.thread_id, "status": task.status.value,
            "stop_reason": task.stop_reason, "objective": task.contract.objective,
            "interaction_mode": task.contract.interaction_mode,
            "message_count": len(messages), "event_count": len(events),
            "latest_checkpoint": None if not checkpoints else {
                "id": checkpoints[-1].id, "label": checkpoints[-1].label,
                "message_sequence": checkpoints[-1].message_sequence,
                "event_sequence": checkpoints[-1].event_sequence,
                "metadata": dict(checkpoints[-1].metadata),
            },
            "notes": tuple({"path": n["path"], "revision": n["revision"]} for n in notes),
            "notes_coverage": {"message_sequence": covered_message, "event_sequence": covered_event},
            "notes_lagging": covered_message < latest_message or covered_event < latest_event,
            "pending_followups": len(followups), "execution_owner": owner,
            "unresolved_tool_calls": _unresolved_tool_calls(messages),
            "verification_evidence_count": len(await self.list_verification_evidence(task_id)),
        }


def _execution_owner(connection: sqlite3.Connection, task_id: str) -> dict[str, object] | None:
    row = connection.execute("SELECT instance_id, owner_pid, owner_create_time, started_at FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
    if row is None: return None
    return {"instance_id": row["instance_id"], "owner_pid": row["owner_pid"], "owner_create_time": row["owner_create_time"], "started_at": row["started_at"]}


def _unresolved_tool_calls(messages: tuple[object, ...]) -> tuple[dict[str, str], ...]:
    """Treat an assistant call without a durable tool result as unknown, never completed."""
    results = {getattr(item.message, "tool_call_id", None) for item in messages if getattr(item.message, "role", None) == "tool"}
    unresolved = []
    for item in messages:
        message = getattr(item, "message", None)
        if getattr(message, "role", None) != "assistant": continue
        for call in getattr(message, "tool_calls", ()):
            if call.id not in results:
                unresolved.append({"tool_call_id": call.id, "tool_name": call.name, "status": "unknown"})
    return tuple(unresolved)
