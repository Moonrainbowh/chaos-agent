from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.attachment_budget import message_tokens  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.errors import ContextBudgetError  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.models import Message  # noqa: E402


class AttachmentBudgetTests(unittest.TestCase):
    def image(self, width: int = 1, height: int = 1) -> AttachmentRef:
        return AttachmentRef(
            hashlib.sha256(f"{width}x{height}".encode()).hexdigest(),
            "image/png",
            100,
            "screen.png",
            width,
            height,
        )

    def test_attachment_tokens_are_counted_and_latest_cannot_be_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compactor = DeterministicCompactor(
                ContextConfig(root, root, "System", message_tokens=100)
            )
            message = Message("user", attachments=(self.image(),))

            self.assertGreater(message_tokens(message), 100)
            with self.assertRaises(ContextBudgetError):
                compactor.compact((message,), 100)

    def test_truncated_latest_user_retains_attachment_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compactor = DeterministicCompactor(
                ContextConfig(root, root, "System", message_tokens=300)
            )
            attachment = self.image()
            latest = Message(
                "user", "long request " * 200, attachments=(attachment,)
            )

            result = compactor.compact((Message("assistant", "old"), latest), 300)

            self.assertEqual(result.messages[-1].attachments, (attachment,))
            self.assertLessEqual(result.estimated_tokens, 300)


if __name__ == "__main__":
    unittest.main()
