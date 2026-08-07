from __future__ import annotations

import unittest

from code_agent.core.attachments import AttachmentRef
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


def _image(name: str, marker: str) -> AttachmentRef:
    return AttachmentRef(marker * 64, "image/png", 16, name, 2, 3)


class _MultipleImages:
    def ingest_paths(
        self, paths: tuple[str, ...], *, explicit_external: bool
    ) -> tuple[AttachmentRef, ...]:
        raise AssertionError("path ingestion is not expected")

    def ingest_clipboard(self) -> AttachmentRef:
        return _image("one.png", "a")

    def ingest_clipboard_items(self, **_: int) -> tuple[AttachmentRef, ...]:
        return (_image("one.png", "a"), _image("two.png", "b"))


class MultiImagePasteTests(unittest.IsolatedAsyncioTestCase):
    def app(self, *, validate=None):
        draft = AttachmentDraft(_MultipleImages(), validate=validate)
        engine = FakeEngine(())
        application = WindowsTerminalApp(
            AgentController(engine), ApprovalBroker(),
            attachment_draft=draft, write=lambda _: None,
        )
        return application, engine, draft

    async def test_terminal_ctrl_v_events_stage_all_images(self) -> None:
        for key in ("\x1b[200~\x1b[201~", "\x16"):
            with self.subTest(key=repr(key)):
                app, engine, draft = self.app()
                await app.handle_key(key)

                self.assertEqual(
                    tuple(item.display_name for item in draft.items),
                    ("one.png", "two.png"),
                )
                self.assertEqual(app.input.text, "")
                self.assertEqual(engine.calls, [])
                self.assertIn(
                    "clipboard images staged · 2", app.state.entries[-1].text
                )

    async def test_attach_clipboard_command_stages_whole_batch(self) -> None:
        app, engine, draft = self.app()

        self.assertTrue(await app.submit("/attach clipboard"))

        self.assertEqual(len(draft.items), 2)
        self.assertEqual(engine.calls, [])
        self.assertIn("clipboard images staged · 2", app.state.entries[-1].text)

    async def test_visual_submission_forwards_both_images(self) -> None:
        app, engine, draft = self.app()
        await app.handle_key("\x1b[200~\x1b[201~")

        self.assertTrue(await app.submit("compare"))
        await app.wait_idle()

        self.assertEqual(engine.attachments, draft.items)
        self.assertEqual(len(engine.attachments), 2)

    async def test_text_only_model_rejects_submit_and_keeps_both_images(self) -> None:
        def reject(items: tuple[AttachmentRef, ...]) -> None:
            if any(item.media_type == "image/png" for item in items):
                raise RuntimeError("current model does not support image input")

        app, engine, draft = self.app(validate=reject)
        await app.handle_key("\x1b[200~\x1b[201~")

        self.assertFalse(await app.submit("compare"))

        self.assertEqual(len(draft.items), 2)
        self.assertEqual(engine.calls, [])
        self.assertIn("does not support image input", app.state.entries[-1].text)


if __name__ == "__main__":
    unittest.main()
