from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.rewind_repository import RewindSessionRepository


class EditBatchApiValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_and_settlement_require_state_enums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RewindSessionRepository(
                Path(temporary) / "sessions.sqlite3"
            )
            with self.assertRaises(TypeError):
                await repository.transition_edit_batch(  # type: ignore[arg-type]
                    "missing", "applying"
                )
            with self.assertRaises(TypeError):
                await repository.settle_edit_batch(  # type: ignore[arg-type]
                    "missing", "completed"
                )


if __name__ == "__main__":
    unittest.main()
