from __future__ import annotations

import sqlite3
from code_agent.core.pending_actions import pending_calls

from ._records import _text


class RecoveryRepositoryMixin:
    """Build a bounded, read-only recovery fact sheet from durable records."""

    async def recovery_checklist(self, task_id: str) -> dict[str, object]:
        task_id = _text(task_id, "task_id")
        task = await self.load_task(task_id)
        from ._history_recovery import recovery_auxiliary
        auxiliary = await self._database.read(lambda c: recovery_auxiliary(c,task_id,task.thread_id))
        notes = auxiliary["notes"]
        stats = await self.history_stats(task.thread_id)
        latest_message = stats["message_sequence"]
        latest_event = stats["event_sequence"]
        covered_message = max((int(n.get("coverage", {}).get("message_sequence", 0)) for n in notes), default=0)
        covered_event = max((int(n.get("coverage", {}).get("event_sequence", 0)) for n in notes), default=0)
        owner = await self._database.read(lambda connection: _execution_owner(connection, task_id))  # type: ignore[attr-defined]
        from ._action_recovery import recovery_snapshot
        snapshot = await self._database.read(lambda connection: recovery_snapshot(connection, task_id))
        return {
            "task_id": task.id, "thread_id": task.thread_id, "status": task.status.value,
            "stop_reason": task.stop_reason, "objective": task.contract.objective,
            "interaction_mode": task.contract.interaction_mode,
            "message_count": stats["message_count"], "event_count": stats["event_count"],
            "latest_checkpoint": auxiliary["latest_checkpoint"],
            "notes": tuple({"path": n["path"], "revision": n["revision"]} for n in notes),
            "notes_coverage": {"message_sequence": covered_message, "event_sequence": covered_event},
            "notes_lagging": covered_message < latest_message or covered_event < latest_event,
            "pending_followups": auxiliary["pending_followups"], "execution_owner": owner,
            "unresolved_tool_calls": tuple({"tool_call_id":item["tool_call_id"],"tool_name":item["tool_name"],"status":"unknown"} for item in snapshot[5]),
            "recovery_version": snapshot[4], "pending_action_records": snapshot[5],
            "verification_evidence_count": auxiliary["verification_evidence_count"],
        }

    async def resolve_pending_action(self, task_id: str, *, call_id: str, message_sequence: int,
            version: str, workspace_root: str, decision: str, reason: str, evidence: str,
            operator_authorized: bool = False, workspace_fingerprint: str | None = None, owner_alive=None):
        """Explicit reconciliation permission is separate from normal task execution.

        Call only from an authenticated operator surface, never register as a model tool.
        A missing/ambiguous receipt keeps the action unresolved; no action is executed here.
        """
        if operator_authorized is not True:
            raise PermissionError("explicit operator reconciliation authorization is required")
        for name, value, limit in (("task_id", task_id, 1024), ("call_id", call_id, 1024),
                ("version", version, 64), ("workspace_root", workspace_root, 4096),
                ("reason", reason, 2048), ("evidence", evidence, 8192)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"invalid {name}")
        if type(message_sequence) is not int or message_sequence <= 0:
            raise ValueError("invalid message sequence")
        from ._action_recovery import resolve_action
        return await self._database.write(lambda connection: resolve_action(connection,
            task_id=task_id, call_id=call_id, message_sequence=message_sequence,
            version=version, workspace_root=workspace_root, decision=decision,
            reason=reason, evidence=evidence, workspace_fingerprint=workspace_fingerprint, owner_alive=owner_alive))

    async def recovery_mutation_receipt(self, task_id: str, call_id: str):
        """Read trusted local mutation facts; Host must separately verify live workspace files."""
        from ._action_recovery import mutation_receipt
        return await self._database.read(lambda connection: mutation_receipt(connection, task_id, call_id))


def _execution_owner(connection: sqlite3.Connection, task_id: str) -> dict[str, object] | None:
    row = connection.execute("SELECT instance_id, owner_pid, owner_create_time, started_at FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
    if row is None: return None
    return {"instance_id": row["instance_id"], "owner_pid": row["owner_pid"], "owner_create_time": row["owner_create_time"], "started_at": row["started_at"]}


def _unresolved_tool_calls(messages: tuple[object, ...]) -> tuple[dict[str, str], ...]:
    """Treat an assistant call without a durable tool result as unknown, never completed."""
    return tuple({"tool_call_id": call.id, "tool_name": call.name, "status": "unknown"}
                 for call in pending_calls(tuple(item.message for item in messages)))
