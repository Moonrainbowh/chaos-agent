from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.checkpoint_tui import RewindStage  # noqa: E402
from code_agent.sessions.models import CheckpointRecord  # noqa: E402
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp  # noqa: E402


NOW = datetime(2026, 7, 22, 10, tzinfo=timezone.utc)


class _Capability:
    def lines(self) -> tuple[str, ...]:
        return ()


class _CanonicalCheckpoints:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.record = CheckpointRecord(
            "cp-1",
            "thread-1",
            "before edit",
            {"inventory_digest": "a" * 64},
            NOW,
        )

    async def list(self, task_id: str):
        self.calls.append(("list", task_id))
        return (self.record,)


class _LegacyRewindTrap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_candidates(self, *args, **kwargs):
        self.calls.append("list_candidates")
        raise AssertionError("legacy preview-only rewind must not receive /rewind")

    async def preview(self, *args, **kwargs):
        self.calls.append("preview")
        raise AssertionError("legacy preview-only rewind must not receive /rewind")


def _app(
    checkpoints: object | None,
    rewind: object | None,
) -> ModeAwareWindowsTerminalApp:
    return ModeAwareWindowsTerminalApp(
        object(),
        object(),
        capability=_Capability(),
        checkpoints=checkpoints,
        rewind=rewind,
        write=lambda value: None,
    )


class RewindTuiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_english_rewind_routes_to_canonical_checkpoint_flow(self) -> None:
        checkpoints = _CanonicalCheckpoints()
        legacy = _LegacyRewindTrap()
        app = _app(checkpoints, legacy)
        app.active_task_id = "task-1"

        handled = await app.submit("/rewind cp-1")

        self.assertTrue(handled)
        self.assertEqual(checkpoints.calls, [("list", "task-1")])
        self.assertEqual(legacy.calls, [])
        self.assertIs(app.rewind, legacy)
        self.assertEqual(app._rewind_flow.stage, RewindStage.MODE)
        self.assertEqual(app._rewind_flow.checkpoint.id, "cp-1")

    async def test_english_rewind_without_checkpoint_control_is_unavailable(self) -> None:
        legacy = _LegacyRewindTrap()
        app = _app(None, legacy)
        app.active_task_id = "task-1"

        handled = await app.submit("/rewind")

        self.assertFalse(handled)
        self.assertIn("unknown or unavailable", app.state.transcript[-1])
        self.assertEqual(legacy.calls, [])

    async def test_english_rewind_without_id_opens_canonical_picker(self) -> None:
        checkpoints = _CanonicalCheckpoints()
        app = _app(checkpoints, _LegacyRewindTrap())
        app.state.task_id = "task-from-state"

        handled = await app.submit("/rewind")

        self.assertTrue(handled)
        self.assertEqual(checkpoints.calls, [("list", "task-from-state")])
        self.assertEqual(app._rewind_flow.stage, RewindStage.CHECKPOINT)


if __name__ == "__main__":
    unittest.main()
