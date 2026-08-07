from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from code_agent.attachments.errors import AttachmentError
from code_agent.attachments.ingest import AttachmentIngestor
from code_agent.attachments.store import AttachmentStore
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard


class MultiImageClipboardRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_ctrl_v_stages_real_clipboard_file_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            paths = (workspace / "one.png", workspace / "two.jpg")
            for index, path in enumerate(paths, start=1):
                Image.new("RGB", (index, 2), (10, 20, 30)).save(path)
            ingestor = AttachmentIngestor(
                AttachmentStore(root / "attachments"),
                workspace_guard=WorkspacePathGuard(workspace),
                ignore_rules=IgnoreRules(),
            )
            draft = AttachmentDraft(ingestor)
            engine = FakeEngine(())
            app = WindowsTerminalApp(
                AgentController(engine), ApprovalBroker(),
                attachment_draft=draft, write=lambda _: None,
            )

            with patch(
                "PIL.ImageGrab.grabclipboard",
                return_value=[str(path) for path in paths],
            ):
                await app.handle_key("\x1b[200~\x1b[201~")

        self.assertEqual(
            tuple(item.display_name for item in draft.items),
            ("one.png", "two.png"),
        )
        self.assertEqual(
            tuple((item.width, item.height) for item in draft.items),
            ((1, 2), (2, 2)),
        )
        self.assertEqual(app.input.text, "")
        self.assertEqual(engine.calls, [])
        self.assertIn("clipboard images staged · 2", app.state.entries[-1].text)

    async def test_overflowing_clipboard_batch_publishes_no_orphan_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            store = AttachmentStore(root / "attachments")
            ingestor = AttachmentIngestor(
                store,
                workspace_guard=WorkspacePathGuard(workspace),
                ignore_rules=IgnoreRules(),
            )
            draft = AttachmentDraft(ingestor)
            texts = tuple(workspace / f"note-{index}.txt" for index in range(7))
            for index, path in enumerate(texts):
                path.write_text(f"note {index}", encoding="utf-8")
            await draft.add_paths(tuple(str(path) for path in texts))
            images = (workspace / "one.png", workspace / "two.png")
            for path in images:
                Image.new("RGB", (1, 1), (10, 20, 30)).save(path)
            before = tuple(store.root.rglob("*.blob"))

            with (
                patch(
                    "PIL.ImageGrab.grabclipboard",
                    return_value=[str(path) for path in images],
                ),
                self.assertRaisesRegex(AttachmentError, "too many attachments"),
            ):
                await draft.add_clipboard_items()

            self.assertEqual(len(draft.items), 7)
            self.assertEqual(tuple(store.root.rglob("*.blob")), before)


if __name__ == "__main__":
    unittest.main()
