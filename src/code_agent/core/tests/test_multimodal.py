from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.models import Message, ModelEvent, ModelEventKind  # noqa: E402
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


class CoreMultimodalTests(unittest.IsolatedAsyncioTestCase):
    def attachment(self) -> AttachmentRef:
        data = b"normalized-png"
        return AttachmentRef(
            hashlib.sha256(data).hexdigest(),
            "image/png",
            len(data),
            "screen.png",
            1,
            1,
        )

    async def test_attachment_only_input_is_persisted_and_context_sees_it_once(self) -> None:
        attachment = self.attachment()
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        model = FakeModelClient(
            ((ModelEvent(ModelEventKind.COMPLETED),),)
        )
        engine = AgentEngine(
            model, context, FakeActionDispatcher(), sessions
        )

        events = [
            event async for event in engine.run("", attachments=(attachment,))
        ]

        user = Message("user", attachments=(attachment,))
        self.assertEqual(context.calls[0][:2], ((user,), ""))
        self.assertEqual(model.calls[0][1], (user,))
        self.assertEqual(sessions.messages["thread-1"][0], user)
        self.assertEqual(
            sum(message == user for message in sessions.messages["thread-1"]), 1
        )
        self.assertNotIn("normalized-png", str(events))

    def test_attachment_json_is_backward_compatible_and_role_restricted(self) -> None:
        attachment = self.attachment()
        message = Message("user", "inspect", attachments=(attachment,))
        self.assertEqual(Message.from_dict(message.to_dict()), message)
        self.assertEqual(Message.from_dict({"role": "user", "content": "old"}).attachments, ())
        with self.assertRaises(ValueError):
            Message("assistant", attachments=(attachment,))


if __name__ == "__main__":
    unittest.main()
