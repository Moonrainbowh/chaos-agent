from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.edit_batch_models import EditBatchPrepare, EditBatchState
from code_agent.sessions.errors import SessionStorageError
from code_agent.sessions.rewind_models import (
    RewindMutationPrepare,
    RewindMutationStatus,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository

from code_agent.sessions.tests.test_edit_batches import (
    FINGERPRINT,
    PLAN_DIGEST,
    _move,
    _rewind_paths,
)


class EditBatchLegacyTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_terminal_api_cannot_bypass_batch_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RewindSessionRepository(
                Path(temporary) / "sessions.sqlite3"
            )
            owner = await repository.create_thread()
            coverage = await repository.ensure_rewind_coverage(FINGERPRINT)
            operation = _move()
            mutation = RewindMutationPrepare(
                coverage.token,
                owner,
                owner,
                None,
                None,
                "legacy-terminal",
                "edit_batch",
                {"identifier": "snapshot-legacy-terminal"},
                _rewind_paths((operation,)),
            )
            prepared = await repository.prepare_edit_batch(
                EditBatchPrepare(
                    mutation, "plan-legacy-terminal", PLAN_DIGEST, (operation,)
                )
            )

            for finish in (
                repository.complete_rewind_mutation,
                repository.abort_rewind_mutation,
            ):
                with self.subTest(finish=finish.__name__):
                    with self.assertRaisesRegex(SessionStorageError, "edit batch"):
                        await finish(prepared.mutation.mutation_id)

            current = await repository.get_edit_batch(
                prepared.mutation.mutation_id
            )
            self.assertEqual(current.state, EditBatchState.PREPARED)
            self.assertEqual(
                current.mutation.status, RewindMutationStatus.PREPARED
            )


if __name__ == "__main__":
    unittest.main()
