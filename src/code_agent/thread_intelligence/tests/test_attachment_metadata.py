from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.thread_intelligence.deterministic_summary import (  # noqa: E402
    render_bounded_source_summary,
)
from code_agent.thread_intelligence.models import (  # noqa: E402
    anchor_message,
    message_digest,
)


class AttachmentMetadataTests(unittest.TestCase):
    def test_digest_and_summary_use_only_safe_reference_metadata(self) -> None:
        digest = hashlib.sha256(b"normalized-png").hexdigest()
        attachment = AttachmentRef(
            digest, "image/png", 14, "screen.png", 10, 20
        )
        message = Message("user", attachments=(attachment,))
        anchored = anchor_message("thread-a", 1, message)

        summary = render_bounded_source_summary((anchored,), 200)

        self.assertEqual(anchored.anchor.digest, message_digest(message))
        self.assertIn("screen.png", summary)
        self.assertIn(digest[:12], summary)
        self.assertNotIn("normalized-png", summary)
        changed = Message(
            "user",
            attachments=(
                AttachmentRef(digest, "image/png", 14, "other.png", 10, 20),
            ),
        )
        self.assertNotEqual(message_digest(message), message_digest(changed))


if __name__ == "__main__":
    unittest.main()
