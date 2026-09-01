from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.attachments.store import AttachmentStore  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle-bound publication")
class AttachmentPublicationIdentityTests(unittest.TestCase):
    def test_path_replacement_after_identity_check_cannot_poison_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AttachmentStore(Path(temporary) / "attachments")
            payload = b"expected attachment payload"
            replacement_blocked: list[bool] = []
            real_rename = os.rename

            def replace_path(_handle: int, path: Path, _destination: Path) -> None:
                displaced = path.with_name(path.name + ".displaced")
                try:
                    real_rename(path, displaced)
                except PermissionError:
                    replacement_blocked.append(True)
                    return
                replacement_blocked.append(False)
                path.write_bytes(b"foreign attachment payload")

            with patch(
                "code_agent._windows_owned_temporary._before_windows_publish",
                side_effect=replace_path,
            ):
                reference = store.put(
                    payload,
                    media_type="text/plain",
                    display_name="note.txt",
                )

            self.assertEqual(replacement_blocked, [True])
            self.assertEqual(store.read(reference), payload)


if __name__ == "__main__":
    unittest.main()
