from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


class AttachmentRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_attachment_round_trips_without_schema_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            thread_id = await repository.create_thread()
            digest = hashlib.sha256(b"png").hexdigest()
            attachment = AttachmentRef(
                digest, "image/png", 3, "screen.png", 1, 1
            )
            message = Message("user", "inspect", attachments=(attachment,))

            await repository.append_message(thread_id, message)

            self.assertEqual(await repository.load_messages(thread_id), (message,))
            payload = message.to_dict()
            self.assertNotIn("path", str(payload))
            self.assertNotIn("base64", str(payload))


if __name__ == "__main__":
    unittest.main()
