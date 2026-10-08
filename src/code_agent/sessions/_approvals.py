"""CAS ledger for the central broker; never executes or authorizes tools."""
from __future__ import annotations
import json
import math
import time
import uuid
from collections.abc import Mapping
from ._approval_binding import binding, record, text


class ApprovalRepositoryMixin:
    async def approval_binding(self, task_id: str, workspace_root: str, *, kind: str = "approval") -> dict:
        return await self._database.read(lambda c: binding(c, task_id, workspace_root, kind))

    async def create_approval_request(self, *, request_id: str, action_id: str,
            action_digest: str, preview: Mapping, binding: Mapping, expires_at: float) -> dict:
        """Freeze a Host-created UUID and bounded JSON preview against current execution."""
        text(request_id, "request_id", 64)
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError) as error:
            raise ValueError("request_id must be a Host UUID") from error
        text(action_id, "action_id")
        text(action_digest, "action_digest", 64)
        if len(action_digest) != 64 or any(c not in "0123456789abcdef" for c in action_digest):
            raise ValueError("invalid action digest")
        if not isinstance(preview, Mapping) or not isinstance(binding, Mapping):
            raise TypeError("preview and binding must be mappings")
        payload = json.dumps(dict(preview), ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(payload.encode()) > 8192:
            raise ValueError("approval preview exceeds capacity")
        if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
            raise ValueError("invalid expiration")
        frozen = dict(binding)
        def write(c):
            now = time.time()
            if not now < expires_at <= now + 3600:
                raise ValueError("approval TTL must be within one hour")
            if binding_current(c, frozen) != frozen:
                raise ValueError("approval binding changed")
            old = c.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                existing = record(old)
                if any(existing[k] != v for k, v in frozen.items()) or any(
                        existing[k] != v for k, v in {"action_id": action_id, "action_digest": action_digest,
                        "preview": dict(preview), "expires_at": expires_at}.items()):
                    raise ValueError("approval request identity conflict")
                return existing
            c.execute("INSERT INTO approval_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, frozen["task_id"], frozen["thread_id"], action_id, action_digest,
                 frozen["workspace_root"], frozen["state_version"], frozen["owner_instance_id"],
                 payload, expires_at, now, "pending", None, None, frozen["kind"], None, None))
            return record(c.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone())
        return await self._database.write(write)

    async def list_approval_requests(self, task_id: str, *, limit: int = 100) -> tuple:
        """Bounded durable snapshot with pending entries reconciled to current facts."""
        text(task_id, "task_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("approval limit must be 1..100")
        def write(c):
            rows = c.execute("SELECT * FROM approval_requests WHERE task_id=? ORDER BY created_at DESC,request_id LIMIT ?", (task_id, limit)).fetchall()
            values = []
            for row in rows:
                value = record(row)
                reconcile(c, value)
                values.append(value)
            return tuple(values)
        return await self._database.write(write)

    async def consume_approval_request(self, request_id: str, *, task_id: str,
            action_digest: str, state_version: str, owner_instance_id: str, approved: bool,
            decision_transition: str | None = None) -> dict:
        """Consume once under transaction CAS; exact valid retries are idempotent."""
        for key, val in (("request_id", request_id), ("task_id", task_id),
                ("action_digest", action_digest), ("state_version", state_version),
                ):
            text(val, key)
        if not isinstance(owner_instance_id, str) or len(owner_instance_id) > 1024:
            raise ValueError("invalid owner_instance_id")
        if type(approved) is not bool:
            raise TypeError("approved must be bool")
        if decision_transition not in {None, "accepted_partial", "failed"}:
            raise ValueError("invalid decision transition")
        def write(c):
            row = c.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None, "approval request not found"
            value = record(row)
            expected = {"task_id": task_id, "action_digest": action_digest,
                        "state_version": state_version, "owner_instance_id": owner_instance_id}
            if any(value[k] != v for k, v in expected.items()):
                return None, "approval response binding mismatch"
            if decision_transition is not None and (value["kind"] != "decision" or not approved):
                return None, "transition requires approved task decision"
            reconcile(c, value)
            try:
                current = binding_current(c, value, allow_terminal=value["post_state_version"] is not None)
            except ValueError:
                current = None
            expected_binding = {**value, "state_version": value["post_state_version"] or value["state_version"]}
            if current is None or any(current[k] != expected_binding[k] for k in current) or time.time() >= value["expires_at"]:
                return None, "approval expired or state/owner changed"
            if value["status"] in {"approved", "denied"}:
                return (reply(c, value, False), None) if value["response"] is approved and value["decision_transition"] == decision_transition else (None, "approval response conflict")
            if value["status"] != "pending":
                return None, "approval no longer pending"
            c.execute("UPDATE approval_requests SET status=?,response=?,consumed_at=? WHERE request_id=? AND status='pending'",
                ("approved" if approved else "denied", int(approved), time.time(), request_id))
            if decision_transition is not None:
                from ._task_records import _task_row, row_task
                from ._codec import encode_task, encode_datetime
                from code_agent.core.task import TaskStatus
                task = row_task(_task_row(c, "id", task_id))
                if task.status is not TaskStatus.WAITING_DECISION:
                    raise ValueError("task decision transition requires waiting_decision")
                changed = task.transition(TaskStatus(decision_transition), "explicit operator decision")
                c.execute("UPDATE tasks SET contract=?,status=?,stop_reason=?,updated_at=? WHERE id=?",
                    (encode_task(changed), changed.status.value, changed.stop_reason, encode_datetime(changed.updated_at), task_id))
                post = binding_current(c, value, allow_terminal=True)["state_version"]
                c.execute("UPDATE approval_requests SET decision_transition=?,post_state_version=? WHERE request_id=?",
                    (decision_transition, post, request_id))
            return reply(c, record(c.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()), True), None
        value, error = await self._database.write(write)
        if error:
            raise ValueError(error)
        return value

    async def invalidate_approval_requests(self, *, request_id: str | None = None, owner_instance_id: str | None = None,
            current_owner_instance_id: str | None = None) -> int:
        """Invalidate old broker instances without deleting the durable cards."""
        if sum(v is not None for v in (request_id, owner_instance_id, current_owner_instance_id)) != 1:
            raise ValueError("provide exactly one owner selector")
        selected = text(request_id or owner_instance_id or current_owner_instance_id, "approval selector")
        column = "request_id" if request_id is not None else "owner_instance_id"
        op = "=" if owner_instance_id is not None else "<>"
        kind_filter = "" if request_id is not None else " AND kind='approval'"
        return await self._database.write(lambda c: c.execute(
            f"UPDATE approval_requests SET status='stale' WHERE status='pending' AND {column} {op if request_id is None else '='} ?{kind_filter}", (selected,)).rowcount)


def binding_current(c, frozen, *, allow_terminal=False):
    return binding(c, frozen["task_id"], frozen["workspace_root"], frozen.get("kind", "approval"), allow_terminal=allow_terminal)


def reply(c, value, consumed_now):
    if value["decision_transition"] is not None:
        task = c.execute("SELECT status,updated_at FROM tasks WHERE id=?", (value["task_id"],)).fetchone()
        value = {**value, "task_status": task["status"], "task_updated_at": task["updated_at"]}
    return {**value, "consumed_now": consumed_now}


def reconcile(c, value):
    if value["status"] != "pending":
        return
    status = "expired" if time.time() >= value["expires_at"] else "pending"
    if status == "pending":
        try:
            current = binding_current(c, value)
            if any(current[k] != value[k] for k in current):
                status = "stale"
        except ValueError:
            status = "stale"
    if status != "pending":
        c.execute("UPDATE approval_requests SET status=? WHERE request_id=? AND status='pending'", (status, value["request_id"]))
        value["status"] = status
