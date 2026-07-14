from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from code_agent.core.events import AgentEvent
from code_agent.core.models import ActionRequest, ActionResult, Message, Usage
from code_agent.core.task_state import TaskState, reduce_task_state
from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.task import TaskContract, TaskRecord, TaskStatus

from ._codec import (
    decode_datetime,
    decode_event,
    decode_message,
    decode_task_state,
    encode_datetime,
    encode_event,
    encode_message,
    encode_task_state,
    encode_task,
    decode_task,
    utc_now,
)
from ._database import SessionDatabase
from ._records import RecordRepositoryMixin, _require_thread, _text, _touch_thread
from .errors import SessionCorruptionError, SessionNotFound
from .models import ThreadStatus, ThreadSummary
from . import _task_budget
from . import _task_execution
from . import _evidence_ledger


class SQLiteSessionRepository(RecordRepositoryMixin):
    """Persist core sessions with one SQLite transaction per async operation."""

    def __init__(self, database_path: str | object) -> None:
        self._database = SessionDatabase(database_path)  # type: ignore[arg-type]

    async def create_thread(self, title: Optional[str] = None) -> str:
        if title is not None:
            title = _text(title, "title")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT INTO threads(id, created_at, updated_at, title, status) VALUES (?, ?, ?, ?, ?)",
                (
                    identifier,
                    timestamp,
                    timestamp,
                    title,
                    ThreadStatus.ACTIVE.value,
                ),
            )

        await self._database.write(write)
        return identifier

    async def create_task(self, thread_id: str, contract: TaskContract) -> TaskRecord:
        if not isinstance(contract, TaskContract):
            raise TypeError("contract must be a TaskContract")
        record = TaskRecord.new(thread_id, contract.objective, contract.authorization, max_active_seconds=contract.max_active_seconds, max_repair_cycles=contract.max_repair_cycles, max_repeated_failure_signatures=contract.max_repeated_failure_signatures)
        def write(connection: sqlite3.Connection) -> TaskRecord:
            _require_thread(connection, record.thread_id)
            connection.execute("INSERT INTO tasks(id, thread_id, contract, status, stop_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (record.id, record.thread_id, encode_task(record), record.status.value, record.stop_reason, encode_datetime(record.created_at), encode_datetime(record.updated_at)))
            return record
        return await self._database.write(write)

    async def load_task(self, task_id: str) -> TaskRecord:
        def read(connection: sqlite3.Connection) -> TaskRecord:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE id = ?", (_text(task_id, "task_id"),)).fetchone()
            if row is None: raise SessionNotFound("task not found")
            return _row_task(row)
        return await self._database.read(read)

    async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None:
        def read(connection: sqlite3.Connection) -> TaskRecord | None:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE thread_id = ?", (_text(thread_id, "thread_id"),)).fetchone()
            return None if row is None else _row_task(row)
        return await self._database.read(read)

    async def transition_task(self, task_id: str, status: TaskStatus, reason: str | None = None) -> TaskRecord:
        def write(connection: sqlite3.Connection) -> TaskRecord:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE id = ?", (_text(task_id, "task_id"),)).fetchone()
            if row is None: raise SessionNotFound("task not found")
            updated = _row_task(row).transition(status, reason)
            connection.execute("UPDATE tasks SET contract = ?, status = ?, stop_reason = ?, updated_at = ? WHERE id = ?", (encode_task(updated), updated.status.value, updated.stop_reason, encode_datetime(updated.updated_at), updated.id))
            return updated
        return await self._database.write(write)

    async def list_tasks(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]:
        def read(connection: sqlite3.Connection) -> tuple[TaskRecord, ...]:
            where = "" if include_terminal else "WHERE status NOT IN ('completed', 'accepted_partial', 'failed')"
            return tuple(_row_task(row) for row in connection.execute(f"SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks {where} ORDER BY updated_at DESC, id").fetchall())
        return await self._database.read(read)

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[Message, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM messages WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_message(row[0]) for row in rows)

        return await self._database.read(read)

    async def append_message(self, thread_id: str, message: Message) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_message(message)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_event(event)
        timestamp = encode_datetime(event.timestamp)

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO events(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, encode_datetime(utc_now()))

        await self._database.write(write)

    async def load_events(self, thread_id: str) -> tuple[AgentEvent, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[AgentEvent, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM events WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_event(row[0]) for row in rows)

        return await self._database.read(read)

    async def load_task_state(self, thread_id: str) -> TaskState:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> TaskState:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT payload FROM task_states WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            return TaskState.empty() if row is None else decode_task_state(row["payload"])

        return await self._database.read(read)

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_task_state(state)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState:
        thread_id = _text(thread_id, "thread_id")
        if not isinstance(request, ActionRequest) or not isinstance(result, ActionResult):
            raise TypeError("request and result must be action values")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> TaskState:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT payload FROM task_states WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            current = TaskState.empty() if row is None else decode_task_state(row["payload"])
            updated = reduce_task_state(current, request, result)
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (thread_id, encode_task_state(updated), timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)
            return updated

        return await self._database.write(write)

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget:
        return await _task_budget.get_or_create(self._database, thread_id, model_name, limits)

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None:
        return await _task_budget.reserve(self._database, thread_id, model_turns=model_turns, tool_calls=tool_calls)

    async def load_task_budget(self, task_id: str) -> TaskBudget:
        return await _task_budget.load(self._database, task_id, self.load_task)

    async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget:
        return await _task_budget.consume_usage(self._database, task_id, usage, self.load_task)

    async def mark_task_budget_warnings(self, task_id: str) -> tuple[int, ...]:
        return await _task_budget.mark_warnings(self._database, task_id, self.load_task)

    async def observe_task_validation(self, task_id: str, fingerprint: str | None, changed_files: int) -> TaskBudget:
        return await _task_budget.observe_validation(self._database, task_id, fingerprint, changed_files, self.load_task)

    async def record_task_active_seconds(self, task_id: str, active_seconds: int) -> TaskBudget:
        return await _task_budget.record_active_seconds(self._database, task_id, active_seconds, self.load_task)

    async def record_task_control(self, task_id: str, instruction: str) -> None:
        task_id = _text(task_id, "task_id")
        instruction = _text(instruction, "instruction")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            connection.execute(
                "INSERT INTO task_controls(task_id, instruction, created_at) VALUES (?, ?, ?)",
                (task_id, instruction, timestamp),
            )

        await self._database.write(write)

    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]:
        task_id = _text(task_id, "task_id")

        def write(connection: sqlite3.Connection) -> tuple[str, ...]:
            row = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            rows = connection.execute(
                "SELECT sequence, instruction FROM task_controls WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            instructions = tuple(_text(row["instruction"], "instruction") for row in rows)
            if rows:
                connection.execute("DELETE FROM task_controls WHERE task_id = ?", (task_id,))
            return instructions

        return await self._database.write(write)

    async def register_task_execution(self, task_id: str, instance_id: str, owner_pid: int, owner_create_time: float) -> None:
        await _task_execution.register(self._database, task_id, instance_id, owner_pid, owner_create_time)

    async def reconcile_stale_tasks(self, owner_alive: _task_execution.OwnerAlive) -> tuple[str, ...]:
        return await _task_execution.reconcile_stale(self._database, owner_alive)

    async def save_task_contract_revision(self, task_id: str, contract: object) -> None:
        await _evidence_ledger.save_contract(self._database, task_id, contract)

    async def load_task_contract_revision(self, task_id: str) -> object:
        return await _evidence_ledger.load_contract(self._database, task_id)

    async def begin_verification_run(self, run_id: str, task_id: str, generation: int, subject_hash: str) -> None:
        await _evidence_ledger.begin_run(self._database, run_id, task_id, generation, subject_hash)

    async def append_verification_evidence(self, run_id: str, task_id: str, evidence: object) -> None:
        await _evidence_ledger.append_evidence(self._database, run_id, task_id, evidence)

    async def close_verification_run(self, run_id: str, status: str) -> None:
        await _evidence_ledger.close_run(self._database, run_id, status)

    async def list_verification_evidence(self, task_id: str) -> tuple[object, ...]:
        return await _evidence_ledger.evidence_for_task(self._database, task_id)

    async def finalize_task(self, task_id: str, run_id: str, contract: object, generation: int, subject_hash: str) -> TaskRecord:
        await _evidence_ledger.finalize_task(
            self._database, task_id, run_id, contract, generation, subject_hash
        )
        return await self.load_task(task_id)

    async def interrupt_open_verification_runs(self, task_id: str) -> int:
        return await _evidence_ledger.interrupt_open_runs(self._database, task_id)

    async def latest_completed_verification_run(self, task_id: str, generation: int, subject_hash: str) -> str | None:
        return await _evidence_ledger.latest_completed_run(
            self._database, task_id, generation, subject_hash
        )

    async def archive_thread(self, thread_id: str) -> None:
        thread_id = _text(thread_id, "thread_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            cursor = connection.execute(
                "UPDATE threads SET status = ?, updated_at = ? WHERE id = ?",
                (ThreadStatus.ARCHIVED.value, timestamp, thread_id),
            )
            if cursor.rowcount != 1:
                raise SessionNotFound(f"thread not found: {thread_id}")

        await self._database.write(write)

    async def list_threads(
        self, *, limit: int = 100, include_archived: bool = False
    ) -> tuple[ThreadSummary, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit <= 0 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a bool")

        def read(connection: sqlite3.Connection) -> tuple[ThreadSummary, ...]:
            where = "" if include_archived else "WHERE t.status = 'active'"
            rows = connection.execute(
                f"""SELECT t.*,
                    (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS message_count,
                    (SELECT payload FROM messages m WHERE m.thread_id = t.id ORDER BY sequence DESC LIMIT 1) AS last_payload
                    FROM threads t {where}
                    ORDER BY t.updated_at DESC, t.id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            try:
                return tuple(_summary(row) for row in rows)
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError("invalid persisted thread") from error

        return await self._database.read(read)


def _summary(row: sqlite3.Row) -> ThreadSummary:
    last_payload = row["last_payload"]
    preview = None
    if last_payload is not None:
        preview = " ".join(decode_message(last_payload).content.split())[:120]
    return ThreadSummary(
        id=row["id"],
        title=row["title"],
        status=ThreadStatus(row["status"]),
        created_at=decode_datetime(row["created_at"], "thread"),
        updated_at=decode_datetime(row["updated_at"], "thread"),
        message_count=row["message_count"],
        last_message_preview=preview,
    )


def _row_task(row: sqlite3.Row) -> TaskRecord:
    try:
        return TaskRecord.from_dict({
            "id": row["id"], "thread_id": row["thread_id"],
            "contract": __import__("json").loads(row["contract"])["contract"],
            "status": row["status"], "stop_reason": row["stop_reason"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task") from error
