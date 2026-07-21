from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from code_agent.core.completion_contract import AcceptanceCriterion, CriterionRequirement, CriterionStrength, TaskContractRevision, TaskIntent
from code_agent.verification.evidence import EvidenceRecord, evidence_satisfies_required
from code_agent.core.task import TaskStatus

from ._codec import encode_datetime, utc_now
from ._records import _text
from .errors import SessionCorruptionError, SessionNotFound


def _contract_payload(contract: TaskContractRevision) -> str:
    return json.dumps({"revision": contract.revision, "intent": contract.intent.value, "criteria": [{"identifier": item.identifier, "description": item.description, "requirement": item.requirement.value, "strength": item.strength.value} for item in contract.criteria]}, sort_keys=True, separators=(",", ":"))


def _decode_contract(payload: str) -> TaskContractRevision:
    try:
        value = json.loads(payload)
        return TaskContractRevision(int(value["revision"]), TaskIntent(value["intent"]), tuple(AcceptanceCriterion(str(item["identifier"]), str(item["description"]), CriterionRequirement(item["requirement"]), CriterionStrength(item["strength"])) for item in value["criteria"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task contract revision") from error


async def save_contract(database: object, task_id: str, contract: TaskContractRevision) -> None:
    task_id = _text(task_id, "task_id")
    if not isinstance(contract, TaskContractRevision):
        raise TypeError("contract must be a TaskContractRevision")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
            raise SessionNotFound("task not found")
        previous = connection.execute("SELECT MAX(revision) FROM task_contract_revisions WHERE task_id = ?", (task_id,)).fetchone()[0]
        if previous is not None and contract.revision <= previous:
            raise ValueError("contract revisions must increase")
        connection.execute("INSERT INTO task_contract_revisions(task_id, revision, payload, created_at) VALUES (?, ?, ?, ?)", (task_id, contract.revision, _contract_payload(contract), timestamp))

    await database.write(write)  # type: ignore[attr-defined]


async def load_contract(database: object, task_id: str) -> TaskContractRevision | None:
    task_id = _text(task_id, "task_id")

    def read(connection: sqlite3.Connection) -> TaskContractRevision | None:
        row = connection.execute("SELECT payload FROM task_contract_revisions WHERE task_id = ? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        return None if row is None else _decode_contract(row["payload"])

    return await database.read(read)  # type: ignore[attr-defined]


async def begin_run(database: object, run_id: str, task_id: str, generation: int, subject_hash: str) -> None:
    run_id, task_id, subject_hash = _text(run_id, "run_id",), _text(task_id, "task_id"), _text(subject_hash, "subject_hash")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("generation must be non-negative")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
            raise SessionNotFound("task not found")
        connection.execute("INSERT INTO verification_runs(id, task_id, generation, subject_hash, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)", (run_id, task_id, generation, subject_hash, timestamp))

    await database.write(write)  # type: ignore[attr-defined]


async def append_evidence(database: object, run_id: str, task_id: str, evidence: EvidenceRecord) -> None:
    run_id, task_id = _text(run_id, "run_id"), _text(task_id, "task_id")
    if not isinstance(evidence, EvidenceRecord):
        raise TypeError("evidence must be an EvidenceRecord")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT status, task_id FROM verification_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["task_id"] != task_id:
            raise SessionNotFound("verification run not found")
        if row["status"] != "running":
            raise ValueError("only running verification runs accept evidence")
        connection.execute("INSERT INTO verification_evidence(id, run_id, task_id, payload, created_at) VALUES (?, ?, ?, ?, ?)", (evidence.identifier, run_id, task_id, json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")), timestamp))

    await database.write(write)  # type: ignore[attr-defined]


async def close_run(database: object, run_id: str, status: str) -> None:
    run_id, status = _text(run_id, "run_id"), _text(status, "status")
    if status not in {"completed", "interrupted", "error"}:
        raise ValueError("invalid verification run status")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        cursor = connection.execute("UPDATE verification_runs SET status = ?, completed_at = ? WHERE id = ? AND status = 'running'", (status, timestamp, run_id))
        if cursor.rowcount != 1:
            raise SessionNotFound("running verification run not found")

    await database.write(write)  # type: ignore[attr-defined]


async def evidence_for_task(database: object, task_id: str) -> tuple[EvidenceRecord, ...]:
    task_id = _text(task_id, "task_id")

    def read(connection: sqlite3.Connection) -> tuple[EvidenceRecord, ...]:
        rows = connection.execute("SELECT payload FROM verification_evidence WHERE task_id = ? ORDER BY created_at, id", (task_id,)).fetchall()
        try:
            return tuple(EvidenceRecord.from_dict(json.loads(row["payload"])) for row in rows)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise SessionCorruptionError("invalid persisted verification evidence") from error

    return await database.read(read)  # type: ignore[attr-defined]


async def finalize_task(
    database: object,
    task_id: str,
    run_id: str,
    contract: TaskContractRevision,
    generation: int,
    subject_hash: str,
) -> None:
    """Atomically record a verified completion after rechecking durable evidence."""
    task_id, run_id, subject_hash = _text(task_id, "task_id"), _text(run_id, "run_id"), _text(subject_hash, "subject_hash")
    if not isinstance(contract, TaskContractRevision):
        raise TypeError("contract must be a TaskContractRevision")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("generation must be non-negative")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        _finalize_write(
            connection, task_id, run_id, contract, generation, subject_hash, timestamp
        )

    await database.write(write)  # type: ignore[attr-defined]


def _finalize_write(
    connection: sqlite3.Connection,
    task_id: str,
    run_id: str,
    contract: TaskContractRevision,
    generation: int,
    subject_hash: str,
    timestamp: str,
) -> None:
    _require_completion_state(
        connection, task_id, run_id, contract, generation, subject_hash
    )
    records = _completion_evidence(connection, task_id, generation, subject_hash)
    latest = {record.criterion_id: record for record in records}
    if any(
        criterion.requirement is CriterionRequirement.REQUIRED
        and not evidence_satisfies_required(
            latest.get(
                criterion.identifier,
                _missing_evidence(criterion.identifier, generation, subject_hash),
            )
        )
        for criterion in contract.criteria
    ):
        raise ValueError("required verification evidence is incomplete")
    connection.execute(
        "UPDATE tasks SET status = ?, stop_reason = NULL, updated_at = ? WHERE id = ?",
        (TaskStatus.COMPLETED.value, timestamp, task_id),
    )
    connection.execute(
        "INSERT INTO task_completions(task_id, revision, generation, subject_hash, "
        "assessment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, contract.revision, generation, subject_hash, "verified", timestamp),
    )


def _require_completion_state(
    connection: sqlite3.Connection,
    task_id: str,
    run_id: str,
    contract: TaskContractRevision,
    generation: int,
    subject_hash: str,
) -> None:
    task = connection.execute(
        "SELECT status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    run = connection.execute(
        "SELECT task_id, generation, subject_hash, status FROM verification_runs "
        "WHERE id = ?",
        (run_id,),
    ).fetchone()
    latest = connection.execute(
        "SELECT payload FROM task_contract_revisions WHERE task_id = ? "
        "ORDER BY revision DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if task is None or run is None or latest is None:
        raise SessionNotFound("task verification state not found")
    if task["status"] != TaskStatus.VERIFYING.value:
        raise ValueError("only verifying tasks can be finalized")
    if run["task_id"] != task_id or run["status"] != "completed":
        raise ValueError("verification run is not completed")
    if run["generation"] != generation or run["subject_hash"] != subject_hash:
        raise ValueError("verification run does not match current subject")
    if _decode_contract(latest["payload"]) != contract:
        raise ValueError("task contract revision is stale")


def _completion_evidence(
    connection: sqlite3.Connection,
    task_id: str,
    generation: int,
    subject_hash: str,
) -> tuple[EvidenceRecord, ...]:
    rows = connection.execute(
        """SELECT evidence.payload
           FROM verification_evidence AS evidence
           JOIN verification_runs AS evidence_run ON evidence_run.id = evidence.run_id
           WHERE evidence.task_id = ?
             AND evidence_run.status = 'completed'
             AND evidence_run.generation = ?
             AND evidence_run.subject_hash = ?
           ORDER BY evidence.created_at, evidence.id""",
        (task_id, generation, subject_hash),
    ).fetchall()
    try:
        return tuple(EvidenceRecord.from_dict(json.loads(row["payload"])) for row in rows)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid completion evidence") from error


def _missing_evidence(criterion_id: str, generation: int, subject_hash: str) -> EvidenceRecord:
    """Create a local non-passing placeholder used only for completion checks."""
    from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance

    return EvidenceRecord.from_output(
        f"missing-{criterion_id}", criterion_id, EvidenceOutcome.ERROR,
        EvidenceProvenance.SYSTEM_VERIFIER, generation, subject_hash, "", "missing evidence",
    )


async def interrupt_open_runs(database: object, task_id: str) -> int:
    """Ensure a recovered task cannot reuse a verifier that was in flight."""
    task_id = _text(task_id, "task_id")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            "UPDATE verification_runs SET status = 'interrupted', completed_at = ? WHERE task_id = ? AND status = 'running'",
            (timestamp, task_id),
        )
        return cursor.rowcount

    return await database.write(write)  # type: ignore[attr-defined]


async def latest_completed_run(
    database: object, task_id: str, generation: int, subject_hash: str
) -> str | None:
    task_id, subject_hash = _text(task_id, "task_id"), _text(subject_hash, "subject_hash")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("generation must be non-negative")

    def read(connection: sqlite3.Connection) -> str | None:
        row = connection.execute(
            "SELECT id FROM verification_runs WHERE task_id = ? AND generation = ? AND subject_hash = ? AND status = 'completed' ORDER BY completed_at DESC, id DESC LIMIT 1",
            (task_id, generation, subject_hash),
        ).fetchone()
        return None if row is None else _text(row["id"], "run_id")

    return await database.read(read)  # type: ignore[attr-defined]
