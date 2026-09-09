import unittest

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tests.test_multi_image_paste import _image
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class Ingestor:
    def __init__(self):
        self.number = 0

    def ingest_paths(self, paths, **kwargs):
        return self.ingest_clipboard_items()

    def ingest_clipboard(self):
        return self.ingest_clipboard_items()[0]

    def ingest_clipboard_items(self, **kwargs):
        self.number += 1
        return (_image(f"image-{self.number}.png", str(self.number)),)


class ImageAtomEditingTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        draft = AttachmentDraft(Ingestor())
        event = AgentEvent(EventKind.MESSAGE_ADDED, {"message": Message("user", "ok").to_dict()})
        engine = FakeEngine((event,))
        output = []
        app = WindowsTerminalApp(AgentController(engine), ApprovalBroker(), attachment_draft=draft, write=output.append)
        return app, draft, engine, output

    async def test_multiple_pastes_numbered_delete_middle_then_submit_remaining(self):
        app, draft, engine, output = self.make_app()
        for _ in range(3):
            await app.handle_key("alt+v")
        self.assertEqual(app.input.display[0], "[image1][image2][image3]")
        await app.handle_key("left")
        await app.handle_key("\x08")
        self.assertEqual(app.input.display[0], "[image1][image3]")
        self.assertEqual([item.display_name for item in draft.items], ["image-1.png", "image-3.png"])
        await app.handle_key("end")
        await app.handle_key("alt+v")
        self.assertEqual(app.input.display[0], "[image1][image3][image4]")
        await app.handle_key(" compare")
        self.assertIn("[image1][image3][image4] compare", output[-1])
        expected = draft.items
        await app.handle_key("\r")
        await app.wait_idle()
        self.assertEqual(engine.attachments, expected)
        self.assertEqual(engine.calls[0][0], " compare")
        self.assertEqual(draft.items, ())
        self.assertNotIn("[image", app.input.display[0])

    async def test_delete_before_marker_and_remove_by_stable_label(self):
        app, draft, engine, _ = self.make_app()
        for _ in range(3):
            await app.handle_key("alt+v")
        await app.handle_key("home")
        await app.handle_key("delete")
        self.assertEqual(app.input.display[0], "[image2][image3]")
        self.assertTrue(await app.submit("/attach remove image2"))
        self.assertEqual(app.input.display[0], "[image3]")
        self.assertEqual(draft.items[0].display_name, "image-3.png")
        self.assertEqual(engine.calls, [])

    async def test_following_text_is_visible_and_deleted_before_image(self):
        app, draft, _, output = self.make_app()
        await app.handle_key("alt+v")
        await app.handle_key("x")
        self.assertIn("[image1]x", output[-1])
        await app.handle_key("\x08")
        self.assertEqual(len(draft.items), 1)
        await app.handle_key("\x08")
        self.assertEqual(draft.items, ())
        self.assertEqual(app.input.display, ("", 0))

    async def test_deletion_repaints_once_with_cursor_hidden_until_positioned(self):
        app, _, _, output = self.make_app()
        await app.handle_key("alt+v")
        await app.handle_key("照片里面有啥")
        output.clear()
        await app.handle_key("\x08")
        self.assertEqual(app.input.display[0], "[image1]照片里面有")
        self.assertEqual(len(output), 1)
        self.assertIn("\x1b[2K", output[0])
        self.assertTrue(output[0].startswith("\x1b[?25l"))
        self.assertTrue(output[0].endswith("\x1b[22C\x1b[?25h"))
        self.assertIn("[image1]照片里面有", output[0])
        self.assertNotIn("照片里面有啥", output[0])
        await app.handle_key("home")
        output.clear()
        await app.handle_key("delete")
        self.assertEqual(app.input.display[0], "照片里面有")
        self.assertEqual(len(output), 1)
        self.assertNotIn("[image1]", output[0])
        self.assertTrue(output[0].endswith("\x1b[4C\x1b[?25h"))

    async def test_clear_removes_both_images_and_paste_atoms(self):
        app, draft, _, _ = self.make_app()
        await app.handle_key("alt+v")
        await app.handle_key("\x1b[200~" + "hidden\n" * 11 + "\x1b[201~")
        await app.handle_key("\x15")
        self.assertEqual(draft.items, ())
        self.assertEqual(app.input.display, ("", 0))

    async def test_typing_after_long_paste_remains_visible_through_real_redraw(self):
        app, _, engine, output = self.make_app()
        pasted = "line\n" * 11
        await app.handle_key("\x1b[200~" + pasted + "\x1b[201~")
        for character in "请分析":
            await app.handle_key(character)
        self.assertIn(f"[chars: {len(pasted)}]请分析", output[-1])
        await app.handle_key("\r")
        await app.wait_idle()
        self.assertEqual(engine.calls[0][0], pasted + "请分析")
