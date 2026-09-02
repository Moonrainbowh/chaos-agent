from __future__ import annotations

import sqlite3
import uuid

from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.models import Message, Usage
from code_agent.core.task import TaskRecord

from . import _evidence_ledger, _task_budget, _task_execution
from ._codec import decode_message, encode_datetime, encode_message, utc_now
from ._records import _text, _touch_thread
from .errors import SessionNotFound


class TaskRuntimeRepositoryMixin:
    _database: object

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget:
        return await _task_budget.get_or_create(
            self._database, thread_id, model_name, limits
        )

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None:
        return await _task_budget.reserve(
            self._database,
            thread_id,
            model_turns=model_turns,
            tool_calls=tool_calls,
        )

    async def load_task_budget(self, task_id: str) -> TaskBudget:
        return await _task_budget.load(self._database, task_id, self.load_task)

    async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget:
        return await _task_budget.consume_usage(
            self._database, task_id, usage, self.load_task
        )

    async def mark_task_budget_warnings(self, task_id: str) -> tuple[int, ...]:
        return await _task_budget.mark_warnings(
            self._database, task_id, self.load_task
        )

    async def observe_task_validation(
        self, task_id: str, fingerprint: str | None, changed_files: int
    ) -> TaskBudget:
        return await _task_budget.observe_validation(
            self._database, task_id, fingerprint, changed_files, self.load_task
        )

    async def record_task_active_seconds(
        self, task_id: str, active_seconds: int
    ) -> TaskBudget:
        return await _task_budget.record_active_seconds(
            self._database, task_id, active_seconds, self.load_task
        )

    async def record_task_control(self, task_id: str, instruction: str) -> None:
        task_id = _text(task_id, "task_id")
        instruction = _text(instruction, "instruction")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            connection.execute(
                "INSERT INTO task_controls(task_id, instruction, created_at) "
                "VALUES (?, ?, ?)",
                (task_id, instruction, timestamp),
            )

        await self._database.write(write)  # type: ignore[attr-defined]

    async def record_task_steering(
        self, task_id: str, message: Message, instruction: str
    ) -> None:
        """Append the user message and its queued control in one transaction."""
        task_id = _text(task_id, "task_id")
        if not isinstance(message, Message) or message.role != "user":
            raise TypeError("steering message must be a user Message")
        payload = encode_message(message)
        instruction = _text(instruction, "instruction")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT thread_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            thread_id = row["thread_id"]
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) "
                "VALUES (?, ?, ?)",
                (thread_id, payload, timestamp),
            )
            connection.execute(
                "INSERT INTO task_controls(task_id, instruction, created_at) "
                "VALUES (?, ?, ?)",
                (task_id, instruction, timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]

    async def record_task_followup(
        self, task_id: str, message: Message, instruction: str
    ) -> str:
        """Persist a user follow-up without exposing it to the current turn."""
        task_id = _text(task_id, "task_id")
        if not isinstance(message, Message) or message.role != "user":
            raise TypeError("follow-up message must be a user Message")
        payload = encode_message(message)
        instruction = _text(instruction, "instruction")
        timestamp = encode_datetime(utc_now())
        identifier = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            if row["status"] in {"completed", "accepted_partial", "failed", "superseded"}:
                raise ValueError("cannot queue input for a terminal task")
            connection.execute(
                "INSERT INTO task_followups(id, task_id, payload, instruction, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, task_id, payload, instruction, timestamp),
            )

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier

    async def promote_task_followups(
        self, task_id: str
    ) -> tuple[tuple[str, Message], ...]:
        """Atomically append queued follow-ups to their task thread in FIFO order."""
        task_id = _text(task_id, "task_id")

        def write(connection: sqlite3.Connection) -> tuple[tuple[str, Message], ...]:
            task = connection.execute(
                "SELECT thread_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise SessionNotFound("task not found")
            rows = connection.execute(
                "SELECT id, payload, created_at FROM task_followups "
                "WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            promoted = tuple(
                (row["id"], decode_message(row["payload"])) for row in rows
            )
            for row, (_, message) in zip(rows, promoted):
                connection.execute(
                    "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                    (task["thread_id"], encode_message(message), row["created_at"]),
                )
            if rows:
                connection.execute(
                    "DELETE FROM task_followups WHERE task_id = ?", (task_id,)
                )
                _touch_thread(connection, task["thread_id"], encode_datetime(utc_now()))
            return promoted

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def list_task_followups(
        self, task_id: str
    ) -> tuple[tuple[str, Message], ...]:
        task_id = _text(task_id, "task_id")

        def read(connection: sqlite3.Connection) -> tuple[tuple[str, Message], ...]:
            if connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone() is None:
                raise SessionNotFound("task not found")
            rows = connection.execute(
                "SELECT id, payload FROM task_followups WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            return tuple((row["id"], decode_message(row["payload"])) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]:
        task_id = _text(task_id, "task_id")

        def write(connection: sqlite3.Connection) -> tuple[str, ...]:
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            rows = connection.execute(
                "SELECT sequence, instruction FROM task_controls "
                "WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            instructions = tuple(
                _text(row["instruction"], "instruction") for row in rows
            )
            if rows:
                connection.execute(
                    "DELETE FROM task_controls WHERE task_id = ?", (task_id,)
                )
            return instructions

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def register_task_execution(
        self,
        task_id: str,
        instance_id: str,
        owner_pid: int,
        owner_create_time: float,
    ) -> None:
        await _task_execution.register(
            self._database, task_id, instance_id, owner_pid, owner_create_time
        )

    async def reconcile_stale_tasks(
        self, owner_alive: _task_execution.OwnerAlive
    ) -> tuple[str, ...]:
        return await _task_execution.reconcile_stale(self._database, owner_alive)

    async def save_task_contract_revision(
        self, task_id: str, contract: object
    ) -> None:
        await _evidence_ledger.save_contract(self._database, task_id, contract)

    async def load_task_contract_revision(self, task_id: str) -> object:
        return await _evidence_ledger.load_contract(self._database, task_id)

    async def begin_verification_run(
        self, run_id: str, task_id: str, generation: int, subject_hash: str
    ) -> None:
        await _evidence_ledger.begin_run(
            self._database, run_id, task_id, generation, subject_hash
        )

    async def append_verification_evidence(
        self, run_id: str, task_id: str, evidence: object
    ) -> None:
        await _evidence_ledger.append_evidence(
            self._database, run_id, task_id, evidence
        )

    async def close_verification_run(self, run_id: str, status: str) -> None:
        await _evidence_ledger.close_run(self._database, run_id, status)

    async def list_verification_evidence(self, task_id: str) -> tuple[object, ...]:
        return await _evidence_ledger.evidence_for_task(self._database, task_id)

    async def finalize_task(
        self,
        task_id: str,
        run_id: str,
        contract: object,
        generation: int,
        subject_hash: str,
    ) -> TaskRecord:
        await _evidence_ledger.finalize_task(
            self._database, task_id, run_id, contract, generation, subject_hash
        )
        return await self.load_task(task_id)

    async def interrupt_open_verification_runs(self, task_id: str) -> int:
        return await _evidence_ledger.interrupt_open_runs(self._database, task_id)

    async def latest_completed_verification_run(
        self, task_id: str, generation: int, subject_hash: str
    ) -> str | None:
        return await _evidence_ledger.latest_completed_run(
            self._database, task_id, generation, subject_hash
        )
