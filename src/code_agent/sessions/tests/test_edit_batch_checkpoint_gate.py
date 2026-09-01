from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchPath,
    EditBatchPrepare,
    EditBatchState,
)
from code_agent.sessions.errors import SessionStorageError
from code_agent.sessions.rewind_models import (
    RewindBaseline,
    RewindMutationPath,
    RewindMutationPrepare,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository


class EditBatchCheckpointGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflicted_batch_keeps_checkpoint_boundary_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RewindSessionRepository(
                Path(temporary) / "sessions.sqlite3"
            )
            owner = await repository.create_thread()
            coverage = await repository.ensure_rewind_coverage("a" * 64)
            path = EditBatchPath(
                "note.txt", True, "b" * 64, 7, True, "c" * 64, 11
            )
            operation = EditBatchOperation(
                EditBatchOperationKind.WRITE, None, path
            )
            mutation = RewindMutationPrepare(
                coverage.token,
                owner,
                owner,
                None,
                None,
                "request",
                "edit_batch",
                {"identifier": "snapshot"},
                (
                    RewindMutationPath(
                        path.path,
                        path.before_existed,
                        path.before_sha256,
                        RewindBaseline.GIT_UNSTAGED,
                        path.after_existed,
                        path.after_sha256,
                    ),
                ),
            )
            batch = await repository.prepare_edit_batch(
                EditBatchPrepare(mutation, "plan", "d" * 64, (operation,))
            )
            await repository.settle_edit_batch(
                batch.mutation.mutation_id,
                EditBatchState.CONFLICTED,
                conflict_code="user-drift",
            )
            with self.assertRaisesRegex(SessionStorageError, "edit batch"):
                await repository.get_rewind_checkpoint_anchor(
                    coverage.token, owner
                )


if __name__ == "__main__":
    unittest.main()
