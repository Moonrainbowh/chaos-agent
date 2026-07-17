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

    async def test_interleaved_messages_are_counted_only_for_thread(self) -> None:
        first = await self.repository.create_thread()
        second = await self.repository.create_thread()
        checkpoint = await self.repository.create_checkpoint(first, "bound")
        await self.repository.append_message(second, Message("user", "other"))
        await self.repository.append_message(first, Message("user", "one"))
        await self.repository.append_message(second, Message("assistant", "other"))
        await self.repository.append_message(first, Message("assistant", "two"))
        observed = await self.repository.observe_rewind(
            first, checkpoint, RewindReadLimits()
        )
        self.assertEqual(observed.conversation_message_count, 2)
        with self.assertRaises(SessionNotFound):
            await self.repository.observe_rewind(
                second, checkpoint, RewindReadLimits()
            )

    async def test_observation_orders_owner_and_foreign_mutations(self) -> None:
        owner = await self.repository.create_thread()
        foreign = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, foreign, foreign, "foreign", (path_fact("f.txt"),)
        )
        await self.completed(
            coverage.token, owner, owner, "owned", (path_fact("o.txt"),)
        )
        observed = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits()
        )
        self.assertEqual(
            tuple(item.owner_thread_id for item in observed.mutations),
            (foreign, owner),
        )
        self.assertEqual(
            tuple(item.sequence for item in observed.mutations), (1, 2)
        )

    async def test_limits_return_no_partial_mutation_chain(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, owner, owner, "one", (path_fact("a.txt"),)
        )
        await self.completed(
            coverage.token,
            owner,
            owner,
            "two",
            (path_fact("b.txt"), path_fact("c.txt")),
        )
        mutation_limited = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits(1, 10)
        )
        path_limited = await self.repository.observe_rewind(
            owner, checkpoint, RewindReadLimits(10, 2)
        )
        self.assertTrue(mutation_limited.limit_exceeded)
        self.assertEqual(mutation_limited.mutations, ())
        self.assertTrue(path_limited.limit_exceeded)
        self.assertEqual(path_limited.mutations, ())

    async def test_missing_trailing_path_fails_closed(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token,
            owner,
            owner,
            "two paths",
            (path_fact("a.txt"), path_fact("b.txt")),
        )
        execute_corruption(
            self.database,
            "DELETE FROM workspace_mutation_paths WHERE ordinal = 1",
        )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )

    async def test_missing_trailing_mutation_rejects_reads_and_prepare(self) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint = await self.repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        await self.completed(
            coverage.token, owner, owner, "first", (path_fact("a.txt"),)
        )
        execute_corruption(
            self.database,
            "DELETE FROM workspace_mutations WHERE request_id = 'first'",
        )
        operations = (
            ("observe", self.repository.observe_rewind(
                owner, checkpoint, RewindReadLimits()
            )),
            ("anchor", self.repository.get_rewind_checkpoint_anchor(
                coverage.token, owner
            )),
            ("ensure", self.repository.ensure_rewind_coverage(FINGERPRINT)),
            ("prepare", self.repository.prepare_rewind_mutation(
                mutation_request(
                    coverage.token, owner, owner, "second",
                    (path_fact("b.txt"),),
                )
            )),
        )
        for name, operation in operations:
            with (
                self.subTest(operation=name),
                self.assertRaises(SessionCorruptionError),
            ):
                await operation

    async def test_heads_capture_later_message_mutation_and_invalidation(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        before = await self.repository.observe_rewind_heads(owner, FINGERPRINT)
        await self.repository.append_message(owner, Message("user", "later"))
        await self.completed(
            coverage.token, owner, owner, "write", (path_fact("a.txt"),)
        )
        await self.repository.record_rewind_gap(
            RewindGapPrepare(
                coverage.token,
                owner,
                owner,
                None,
                None,
                "gap",
                "run_command",
                "unknown-writer",
            )
        )
        after = await self.repository.observe_rewind_heads(owner, FINGERPRINT)
        self.assertGreater(after.message_sequence, before.message_sequence)
        self.assertGreater(after.mutation_sequence, before.mutation_sequence)
        self.assertEqual(after.coverage_state, RewindCoverageState.INVALIDATED)

    async def test_candidate_cursor_is_stable_and_facets_are_presence_only(
        self,
    ) -> None:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        plain = await self.repository.create_checkpoint(owner, "plain")
        anchor = await self.repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        anchored = await self.repository.create_checkpoint(
            owner, "anchored", rewind_anchor=anchor
        )
        timestamp = "2026-07-17T01:02:03Z"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE checkpoints SET created_at = ? WHERE thread_id = ?",
                (timestamp, owner),
            )
        first = await self.repository.list_rewind_candidates(owner, limit=1)
        second = await self.repository.list_rewind_candidates(
            owner, cursor=first.next_cursor, limit=1
        )
        self.assertEqual(
            {first.items[0].checkpoint_id, second.items[0].checkpoint_id},
            {plain, anchored},
        )
        facets = {
            item.checkpoint_id: (item.has_message_bound, item.has_code_anchor)
            for item in first.items + second.items
        }
        self.assertEqual(facets[plain], (True, False))
        self.assertEqual(facets[anchored], (True, True))
        self.assertIsNone(second.next_cursor)

if __name__ == "__main__":
    unittest.main()
