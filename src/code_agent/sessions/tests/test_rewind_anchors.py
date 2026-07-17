from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionCorruptionError,
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.rewind_models import (  # noqa: E402
    RewindBaseline,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindReadLimits,
)
from code_agent.sessions.rewind_repository import (  # noqa: E402
    RewindSessionRepository,
)


FINGERPRINT = "a" * 64


def mutation_request(
    token: object,
    owner: str,
    origin: str,
    request_id: str,
    paths: tuple[RewindMutationPath, ...],
) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        token,  # type: ignore[arg-type]
        owner,
        origin,
        None,
        None,
        request_id,
        "write_file",
        {"identifier": request_id},
        paths,
    )


def path_fact(name: str) -> RewindMutationPath:
    return RewindMutationPath(
        name,
        False,
        None,
        RewindBaseline.ABSENT,
        True,
        "1" * 64,
    )


def execute_corruption(database: Path, statement: str) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


class RewindObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def completed(
        self,
        token: object,
        owner: str,
        origin: str,
        request_id: str,
        paths: tuple[RewindMutationPath, ...],
    ) -> None:
        record = await self.repository.prepare_rewind_mutation(
            mutation_request(token, owner, origin, request_id, paths)
        )
        await self.repository.complete_rewind_mutation(record.mutation_id)

    async def test_child_checkpoint_keeps_conversation_and_code_scopes_separate(
        self,
    ) -> None:
        root = await self.repository.create_thread()
        child = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, root
        )
        checkpoint = await self.repository.create_checkpoint(
            child, "child checkpoint", rewind_anchor=anchor
        )
        observed = await self.repository.observe_rewind(
            child, checkpoint, RewindReadLimits()
        )
        self.assertEqual(observed.checkpoint.thread_id, child)
        self.assertIsNotNone(observed.checkpoint_fact)
        assert observed.checkpoint_fact is not None
        self.assertEqual(observed.checkpoint_fact.owner_thread_id, root)

    async def test_stale_anchor_and_prepared_anchor_are_rejected(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        stale = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        await self.completed(
            coverage.token, owner, owner, "first", (path_fact("a.txt"),)
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.create_checkpoint(
                owner, "stale", rewind_anchor=stale
            )
        await self.repository.prepare_rewind_mutation(
            mutation_request(
                coverage.token,
                owner,
                owner,
                "pending",
                (path_fact("b.txt"),),
            )
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.get_rewind_checkpoint_anchor(
                coverage.token, owner
            )

    async def test_invalidated_anchor_persists_invalidated_fact(self) -> None:
        owner = await self.repository.create_thread()
        active = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        await self.repository.record_rewind_gap(
            RewindGapPrepare(
                active.token,
                owner,
                owner,
                None,
                None,
                "gap",
                "run_command",
                "unknown-writer",
            )
        )
        invalidated = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            invalidated.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "after gap", rewind_anchor=anchor
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        assert observed.checkpoint_fact is not None
        self.assertEqual(
            observed.checkpoint_fact.coverage_state,
            RewindCoverageState.INVALIDATED,
        )

    async def test_unanchored_checkpoint_and_metadata_have_no_code_fact(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        checkpoint = await self.repository.create_checkpoint(
            owner,
            "plain",
            {
                "workspace_fingerprint": FINGERPRINT,
                "owner_thread_id": owner,
                "mutation_sequence": 999,
            },
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        self.assertIsNone(observed.checkpoint_fact)

    async def test_checkpoint_fact_generation_mismatch_fails_closed(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        execute_corruption(
            self.database,
            "UPDATE checkpoint_rewind_facts SET coverage_generation = 2",
        )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )

    async def test_checkpoint_fact_sequence_after_head_fails_closed(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        execute_corruption(
            self.database,
            "UPDATE checkpoint_rewind_facts SET mutation_sequence = 1",
        )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )

    async def test_later_mutation_generation_mismatch_fails_closed(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, owner, owner, "later", (path_fact("a.txt"),)
        )
        execute_corruption(
            self.database,
            "UPDATE workspace_mutations SET coverage_generation = 2",
        )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )

if __name__ == "__main__":
    unittest.main()
