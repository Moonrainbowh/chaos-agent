from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.rewind_models import (  # noqa: E402
    CoverageToken,
    RewindBaseline,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def path_fact(name: str = "note.txt") -> RewindMutationPath:
    return RewindMutationPath(
        name,
        False,
        None,
        RewindBaseline.ABSENT,
        True,
        "1" * 64,
    )


def prepare(
    coverage: CoverageToken,
    owner: str,
    request_id: str,
    *,
    origin: str | None = None,
    task_id: str | None = None,
    parent_request_id: str | None = None,
    action_name: str = "write_file",
    handle: dict[str, object] | None = None,
    paths: tuple[RewindMutationPath, ...] | None = None,
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        coverage,
        owner,
        owner if origin is None else origin,
        task_id,
        parent_request_id,
        request_id,
        action_name,
        {"identifier": "snapshot"} if handle is None else handle,
        (path_fact(),) if paths is None else paths,
    )


def gap(
    coverage: CoverageToken,
    owner: str,
    request_id: str,
    *,
    reason: str = "unknown-workspace-writer",
) -> RewindGapPrepare:
    return RewindGapPrepare(
        coverage,
        owner,
        owner,
        None,
        None,
        request_id,
        "run_command",
        reason,
    )


class RewindMutationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_ensure_coverage_is_idempotent(self) -> None:
        first = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        second = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        self.assertEqual(first, second)
        self.assertEqual(first.generation, 1)
        self.assertEqual(first.state, RewindCoverageState.ACTIVE)
        self.assertEqual(first.mutation_high_water, 0)

    async def test_prepare_retains_owner_origin_task_and_parent(self) -> None:
        owner = await self.repository.create_thread()
        origin = await self.repository.create_thread()
        task = await self.repository.create_task(
            owner,
            TaskContract("edit", TaskAuthorization.local_workspace(".")),
        )
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        record = await self.repository.prepare_rewind_mutation(
            prepare(
                coverage.token,
                owner,
                "child-write",
                origin=origin,
                task_id=task.id,
                parent_request_id="delegate-1",
            )
        )
        self.assertEqual(record.owner_thread_id, owner)
        self.assertEqual(record.origin_thread_id, origin)
        self.assertEqual(record.task_id, task.id)
        self.assertEqual(record.parent_request_id, "delegate-1")
        self.assertEqual(record.status, RewindMutationStatus.PREPARED)
        self.assertEqual(len(record.mutation_id), 32)

    async def test_identical_prepare_is_idempotent(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        request = prepare(coverage.token, owner, "same")
        first = await self.repository.prepare_rewind_mutation(request)
        second = await self.repository.prepare_rewind_mutation(request)
        self.assertEqual(first, second)
        self.assertEqual(first.sequence, 1)

    async def test_reused_key_with_different_payload_is_rejected(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "collision")
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(
                prepare(
                    coverage.token,
                    owner,
                    "collision",
                    action_name="replace_text",
                )
            )
        with sqlite3.connect(self.database) as connection:
            counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutations"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutation_paths"
                ).fetchone()[0],
            )
        self.assertEqual(counts, (1, 1))

    async def test_failed_path_insert_rolls_back_prepare_and_high_water(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TRIGGER reject_second_path BEFORE INSERT "
                "ON workspace_mutation_paths WHEN NEW.ordinal = 1 "
                "BEGIN SELECT RAISE(ABORT, 'reject second path'); END"
            )
        request = prepare(
            coverage.token,
            owner,
            "two-paths",
            paths=(path_fact("a.txt"), path_fact("b.txt")),
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(request)
        after = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with sqlite3.connect(self.database) as connection:
            counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutations"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_mutation_paths"
                ).fetchone()[0],
            )
        self.assertEqual(after.mutation_high_water, 0)
        self.assertEqual(counts, (0, 0))

    async def test_complete_and_abort_accept_only_prepared_records(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        completed = await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "complete")
        )
        aborted = await self.repository.prepare_rewind_mutation(
            prepare(coverage.token, owner, "abort")
        )
        completed = await self.repository.complete_rewind_mutation(
            completed.mutation_id
        )
        aborted = await self.repository.abort_rewind_mutation(
            aborted.mutation_id
        )
        self.assertEqual(completed.status, RewindMutationStatus.COMPLETED)
        self.assertEqual(aborted.status, RewindMutationStatus.ABORTED)
        with self.assertRaises(SessionStorageError):
            await self.repository.abort_rewind_mutation(completed.mutation_id)
        with self.assertRaises(SessionStorageError):
            await self.repository.complete_rewind_mutation(aborted.mutation_id)
        with self.assertRaises(SessionNotFound):
            await self.repository.complete_rewind_mutation("f" * 32)

    async def test_gap_invalidates_and_repeat_gaps_remain_durable(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        first = await self.repository.record_rewind_gap(
            gap(active.token, owner, "gap-1", reason="first-gap")
        )
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        second = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-2", reason="later-gap")
        )
        again = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-2", reason="later-gap")
        )
        after = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        self.assertEqual(first.status, RewindMutationStatus.GAP)
        self.assertEqual(second, again)
        self.assertGreater(second.sequence, first.sequence)
        self.assertEqual(after.state, RewindCoverageState.INVALIDATED)
        self.assertEqual(after.generation, active.generation)
        self.assertEqual(after.invalidation_reason, "first-gap")

    async def test_invalidated_coverage_rejects_prepare_but_accepts_gap(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.record_rewind_gap(gap(active.token, owner, "gap"))
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_rewind_mutation(
                prepare(invalidated.token, owner, "write")
            )
        durable = await self.repository.record_rewind_gap(
            gap(invalidated.token, owner, "gap-after")
        )
        self.assertEqual(durable.status, RewindMutationStatus.GAP)

    async def test_handle_keys_cannot_forge_journal_columns(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        record = await self.repository.prepare_rewind_mutation(
            prepare(
                coverage.token,
                owner,
                "opaque",
                handle={"status": "gap", "sequence": 999, "generation": 999},
            )
        )
        self.assertEqual(record.status, RewindMutationStatus.PREPARED)
        self.assertEqual(record.sequence, 1)
        self.assertEqual(record.coverage.generation, 1)
        self.assertEqual(record.snapshot_handle["status"], "gap")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
