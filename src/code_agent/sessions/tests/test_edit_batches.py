from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from code_agent.sessions.edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchOperationProgress,
    EditBatchPath,
    EditBatchPrepare,
    EditBatchState,
)
from code_agent.sessions.errors import SessionStorageError
from code_agent.sessions.rewind_models import (
    RewindBaseline,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository


FINGERPRINT = "a" * 64
HASH_A = "b" * 64
HASH_B = "c" * 64
PLAN_DIGEST = "d" * 64
SIZE_A = 7
SIZE_B = 11


def _move() -> EditBatchOperation:
    return EditBatchOperation(
        EditBatchOperationKind.MOVE,
        EditBatchPath("src/a.py", True, HASH_A, SIZE_A, False, None, 0),
        EditBatchPath("dst/a.py", False, None, 0, True, HASH_A, SIZE_A),
    )


def _write(path: str = "notes.txt") -> EditBatchOperation:
    return EditBatchOperation(
        EditBatchOperationKind.WRITE,
        None,
        EditBatchPath(path, True, HASH_A, SIZE_A, True, HASH_B, SIZE_B),
    )


def _case_move() -> EditBatchOperation:
    return EditBatchOperation(
        EditBatchOperationKind.MOVE,
        EditBatchPath("src/name.py", True, HASH_A, SIZE_A, False, None, 0),
        EditBatchPath("src/NAME.py", False, None, 0, True, HASH_A, SIZE_A),
        case_only=True,
    )


def _rewind_paths(
    operations: tuple[EditBatchOperation, ...],
) -> tuple[RewindMutationPath, ...]:
    result: list[RewindMutationPath] = []
    for operation in operations:
        if operation.case_only:
            assert operation.source is not None
            result.append(
                RewindMutationPath(
                    operation.source.path,
                    True,
                    operation.source.before_sha256,
                    RewindBaseline.GIT_UNSTAGED,
                    True,
                    operation.source.before_sha256,
                )
            )
            continue
        for endpoint in (operation.source, operation.target):
            if endpoint is None:
                continue
            baseline = (
                RewindBaseline.GIT_UNSTAGED
                if endpoint.before_existed
                else RewindBaseline.ABSENT
            )
            result.append(
                RewindMutationPath(
                    endpoint.path,
                    endpoint.before_existed,
                    endpoint.before_sha256,
                    baseline,
                    endpoint.after_existed,
                    endpoint.after_sha256,
                )
            )
    return tuple(result)


class EditBatchRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = RewindSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def _request(
        self,
        request_id: str,
        operations: tuple[EditBatchOperation, ...] = (_move(),),
        *,
        digest: str = PLAN_DIGEST,
    ) -> EditBatchPrepare:
        owner = await self.repository.create_thread()
        coverage = await self.repository.ensure_rewind_coverage(FINGERPRINT)
        mutation = RewindMutationPrepare(
            coverage.token,
            owner,
            owner,
            None,
            None,
            request_id,
            "edit_batch",
            {"identifier": f"snapshot-{request_id}"},
            _rewind_paths(operations),
        )
        return EditBatchPrepare(
            mutation, f"plan-{request_id}", digest, operations
        )

    async def test_prepare_is_idempotent_and_survives_reopen(self) -> None:
        request = await self._request("one")
        first = await self.repository.prepare_edit_batch(request)
        repeated = await self.repository.prepare_edit_batch(request)
        reopened = RewindSessionRepository(self.database)
        loaded = await reopened.get_edit_batch(first.mutation.mutation_id)

        self.assertEqual(first, repeated)
        self.assertEqual(loaded, first)
        self.assertEqual(first.state, EditBatchState.PREPARED)
        self.assertEqual(first.mutation.status, RewindMutationStatus.PREPARED)
        self.assertEqual(first.operations[0].progress, EditBatchOperationProgress.PENDING)
        self.assertEqual(first.operations[0].operation, _move())

        drifted = EditBatchPrepare(
            request.mutation, request.plan_id, "e" * 64, request.operations
        )
        with self.assertRaisesRegex(SessionStorageError, "idempotency"):
            await self.repository.prepare_edit_batch(drifted)

    async def test_case_only_move_persists_both_companion_endpoints(self) -> None:
        request = await self._request("case", (_case_move(),))
        record = await self.repository.prepare_edit_batch(request)
        self.assertEqual(len(record.mutation.paths), 1)
        self.assertEqual(record.operations[0].operation, _case_move())

    async def test_progress_and_completed_settlement_are_idempotent(self) -> None:
        prepared = await self.repository.prepare_edit_batch(
            await self._request("complete")
        )
        applying = await self.repository.transition_edit_batch(
            prepared.mutation.mutation_id, EditBatchState.APPLYING
        )
        self.assertEqual(
            await self.repository.transition_edit_batch(
                prepared.mutation.mutation_id, EditBatchState.APPLYING
            ),
            applying,
        )
        committed = await self.repository.commit_edit_batch_operation(
            prepared.mutation.mutation_id, 0
        )
        self.assertEqual(
            await self.repository.commit_edit_batch_operation(
                prepared.mutation.mutation_id, 0
            ),
            committed,
        )
        completed = await self.repository.settle_edit_batch(
            prepared.mutation.mutation_id, EditBatchState.COMPLETED
        )
        repeated = await self.repository.settle_edit_batch(
            prepared.mutation.mutation_id, EditBatchState.COMPLETED
        )
        self.assertEqual(completed, repeated)
        self.assertEqual(completed.mutation.status, RewindMutationStatus.COMPLETED)
        with self.assertRaises(SessionStorageError):
            await self.repository.settle_edit_batch(
                prepared.mutation.mutation_id, EditBatchState.ROLLED_BACK
            )

    async def test_operation_progress_is_ordered(self) -> None:
        request = await self._request("ordered", (_write("a.txt"), _write("b.txt")))
        prepared = await self.repository.prepare_edit_batch(request)
        await self.repository.transition_edit_batch(
            prepared.mutation.mutation_id, EditBatchState.APPLYING
        )
        with self.assertRaisesRegex(SessionStorageError, "order"):
            await self.repository.commit_edit_batch_operation(
                prepared.mutation.mutation_id, 1
            )
        await self.repository.commit_edit_batch_operation(
            prepared.mutation.mutation_id, 0
        )
        record = await self.repository.commit_edit_batch_operation(
            prepared.mutation.mutation_id, 1
        )
        self.assertEqual(
            [item.progress for item in record.operations],
            [EditBatchOperationProgress.COMMITTED] * 2,
        )

    async def test_rollback_and_conflict_close_parent_atomically(self) -> None:
        rolled = await self.repository.prepare_edit_batch(
            await self._request("rollback")
        )
        rolling = await self.repository.transition_edit_batch(
            rolled.mutation.mutation_id, EditBatchState.ROLLING_BACK
        )
        self.assertEqual(
            await self.repository.transition_edit_batch(
                rolled.mutation.mutation_id, EditBatchState.ROLLING_BACK
            ),
            rolling,
        )
        rolled = await self.repository.settle_edit_batch(
            rolled.mutation.mutation_id, EditBatchState.ROLLED_BACK
        )
        self.assertEqual(rolled.mutation.status, RewindMutationStatus.ABORTED)

        conflicted = await self.repository.prepare_edit_batch(
            await self._request("conflict")
        )
        conflicted = await self.repository.settle_edit_batch(
            conflicted.mutation.mutation_id,
            EditBatchState.CONFLICTED,
            conflict_code="user-drift",
        )
        self.assertEqual(conflicted.mutation.status, RewindMutationStatus.ABORTED)
        self.assertEqual(conflicted.conflict_code, "user-drift")
        self.assertEqual(
            await self.repository.settle_edit_batch(
                conflicted.mutation.mutation_id,
                EditBatchState.CONFLICTED,
                conflict_code="user-drift",
            ),
            conflicted,
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.settle_edit_batch(
                conflicted.mutation.mutation_id,
                EditBatchState.CONFLICTED,
                conflict_code="other-drift",
            )

    async def test_conflict_stays_unresolved_and_blocks_same_workspace(self) -> None:
        first = await self.repository.prepare_edit_batch(await self._request("first"))
        first = await self.repository.settle_edit_batch(
            first.mutation.mutation_id,
            EditBatchState.CONFLICTED,
            conflict_code="user-drift",
        )
        unresolved = await self.repository.list_unresolved_edit_batches(FINGERPRINT)
        records = await self.repository.list_edit_batches(FINGERPRINT)
        self.assertEqual(unresolved, (first,))
        self.assertEqual(records, (first,))
        with self.assertRaises(SessionStorageError):
            await self.repository.prepare_edit_batch(await self._request("second"))

    async def test_settlement_failure_rolls_back_batch_and_parent(self) -> None:
        prepared = await self.repository.prepare_edit_batch(
            await self._request("atomic")
        )
        await self.repository.transition_edit_batch(
            prepared.mutation.mutation_id, EditBatchState.APPLYING
        )
        await self.repository.commit_edit_batch_operation(
            prepared.mutation.mutation_id, 0
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_parent BEFORE UPDATE ON workspace_mutations "
                "WHEN NEW.status = 'completed' BEGIN "
                "SELECT RAISE(ABORT, 'injected'); END"
            )
        with self.assertRaises(SessionStorageError):
            await self.repository.settle_edit_batch(
                prepared.mutation.mutation_id, EditBatchState.COMPLETED
            )
        current = await self.repository.get_edit_batch(
            prepared.mutation.mutation_id
        )
        self.assertEqual(current.state, EditBatchState.APPLYING)
        self.assertEqual(current.mutation.status, RewindMutationStatus.PREPARED)


if __name__ == "__main__":
    unittest.main()
