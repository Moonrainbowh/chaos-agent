from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402


class AgentControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_streams_the_core_events_without_reinterpreting_them(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        )
        engine = FakeEngine(events)
        controller = AgentController(engine)

        received = [event async for event in controller.ask("inspect")]

        self.assertEqual(received, list(events))
        self.assertEqual(engine.calls, [("inspect", None, None)])

    async def test_resume_requires_thread_id_and_passes_it_to_the_engine(self) -> None:
        engine = FakeEngine(())
        controller = AgentController(engine)

        received = [
            event async for event in controller.resume("thread-1", "continue")
        ]

        self.assertEqual(received, [])
        self.assertEqual(engine.calls, [("continue", "thread-1", None)])
        with self.assertRaises(ValueError):
            _ = [event async for event in controller.resume(" ", "continue")]

    async def test_run_json_emits_one_stable_json_object_per_event(self) -> None:
        event = AgentEvent(EventKind.ERROR, {"code": "model_stream"})
        controller = AgentController(FakeEngine((event,)))

        lines = [line async for line in controller.run_json("inspect")]

        self.assertEqual(lines, [json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))])

    async def test_attachments_are_forwarded_without_changing_plain_text_calls(self) -> None:
        attachment = AttachmentRef("a" * 64, "text/plain", 4, "note.txt")
        engine = FakeEngine(())
        controller = AgentController(engine)

        _ = [
            event
            async for event in controller.ask("inspect", attachments=(attachment,))
        ]

        self.assertEqual(engine.attachments, (attachment,))


if __name__ == "__main__":
    unittest.main()
