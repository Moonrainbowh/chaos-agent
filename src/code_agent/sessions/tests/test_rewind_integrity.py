from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.rewind_models import (  # noqa: E402
    RewindBaseline,
    RewindCoverageRecord,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationRecord,
    RewindReadLimits,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def path_fact(name: str) -> RewindMutationPath:
    return RewindMutationPath(
        name, False, None, RewindBaseline.ABSENT, True, "1" * 64
    )


def mutation_request(
    coverage: object,
    owner: str,
    request_id: str,
    path: str,
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        coverage,  # type: ignore[arg-type]
        owner,
        owner,
        None,
        None,
        request_id,
        "write_file",
        {"identifier": request_id},
        (path_fact(path),),
    )


def gap_request(
    coverage: object,
    owner: str,
    request_id: str,
) -> RewindGapPrepare:
    return RewindGapPrepare(
        coverage,  # type: ignore[arg-type]
        owner,
        owner,
        None,
        None,
        request_id,
        "run_command",
        "unknown-writer",
    )


def execute_sql(database: Path, statement: str) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


class RewindIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def anchored(self) -> tuple[str, RewindCoverageRecord, str]:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        return owner, coverage, checkpoint

    async def completed(
        self,
        coverage: object,
        owner: str,
        request_id: str,
        path: str,
    ) -> RewindMutationRecord:
        prepared = await self.repository.prepare_rewind_mutation(
            mutation_request(coverage, owner, request_id, path)
        )
        return await self.repository.complete_rewind_mutation(
            prepared.mutation_id
        )

    async def test_missing_middle_mutation_rejects_reads(self) -> None:
        owner, coverage, checkpoint = await self.anchored()
        await self.completed(coverage.token, owner, "first", "a.txt")
        await self.completed(coverage.token, owner, "second", "b.txt")
        execute_sql(
            self.database,
            "DELETE FROM workspace_mutations WHERE request_id = 'first'",
        )
        operations = (
            ("ensure", self.repository.ensure_rewind_coverage(FINGERPRINT)),
            ("observe", self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )),
        )
        for name, operation in operations:
            with self.subTest(operation=name), self.assertRaises(
                SessionCorruptionError
            ):
                await operation

    async def test_counts_advance_once_and_anchor_captures_boundary(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        request = mutation_request(coverage.token, owner, "write", "a.txt")
        prepared = await self.repository.prepare_rewind_mutation(request)
        repeated = await self.repository.prepare_rewind_mutation(request)
        await self.repository.complete_rewind_mutation(prepared.mutation_id)
        after_prepare = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        self.assertEqual(repeated, prepared)
        self.assertEqual(after_prepare.mutation_count, 1)
        self.assertEqual(after_prepare.mutation_high_water, prepared.sequence)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            after_prepare.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "boundary", rewind_anchor=anchor
        )
        gap = gap_request(after_prepare.token, owner, "gap")
        first_gap = await self.repository.record_rewind_gap(gap)
        repeated_gap = await self.repository.record_rewind_gap(gap)
        current = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        self.assertEqual(first_gap, repeated_gap)
        self.assertEqual(current.mutation_count, 2)
        self.assertEqual(current.mutation_high_water, first_gap.sequence)
        self.assertEqual(observed.checkpoint_fact.mutation_count, 1)

    async def test_checkpoint_boundary_cannot_skip_existing_mutation(self) -> None:
        owner, coverage, checkpoint = await self.anchored()
        mutation = await self.completed(
            coverage.token, owner, "later", "a.txt"
        )
        execute_sql(
            self.database,
            "UPDATE checkpoint_rewind_facts "
            f"SET mutation_sequence = {mutation.sequence}",
        )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )

    async def test_missing_fact_rejected_by_observe_and_candidates(self) -> None:
        owner, _, checkpoint = await self.anchored()
        execute_sql(self.database, "DELETE FROM checkpoint_rewind_facts")
        operations = (
            ("observe", self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )),
            ("candidates", self.repository.list_rewind_candidates(owner)),
        )
        for name, operation in operations:
            with self.subTest(operation=name), self.assertRaises(
                SessionCorruptionError
            ):
                await operation

    async def test_missing_marker_rejected_by_observe_and_candidates(self) -> None:
        execute_sql(
            self.database,
            "CREATE TABLE IF NOT EXISTS checkpoint_rewind_expectations("
            "checkpoint_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)",
        )
        owner, _, checkpoint = await self.anchored()
        execute_sql(
            self.database, "DELETE FROM checkpoint_rewind_expectations"
        )
        operations = (
            ("observe", self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )),
            ("candidates", self.repository.list_rewind_candidates(owner)),
        )
        for name, operation in operations:
            with self.subTest(operation=name), self.assertRaises(
                SessionCorruptionError
            ):
                await operation

    async def test_legacy_checkpoint_remains_unanchored(self) -> None:
        owner = await self.repository.create_thread()
        checkpoint = await self.repository.create_checkpoint(owner, "legacy")
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        candidates = await self.repository.list_rewind_candidates(owner)
        self.assertIsNone(observed.checkpoint_fact)
        self.assertEqual(candidates.items[0].checkpoint_id, checkpoint)
        self.assertFalse(candidates.items[0].has_code_anchor)


if __name__ == "__main__":
    unittest.main()
