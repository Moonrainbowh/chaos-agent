from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.core.attachments import AttachmentRef
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


def _text_ref(name: str = "note.txt") -> AttachmentRef:
    return AttachmentRef("a" * 64, "text/plain", 8, name)


def _image_ref(name: str = "screen.png") -> AttachmentRef:
    return AttachmentRef("b" * 64, "image/png", 16, name, 2, 3)


class _Ingestor:
    def ingest_paths(
        self, paths: tuple[str, ...], *, explicit_external: bool
    ) -> tuple[AttachmentRef, ...]:
        assert explicit_external
        return tuple(_text_ref(Path(path).name) for path in paths)

    def ingest_clipboard(self) -> AttachmentRef:
        return _image_ref()

    def ingest_clipboard_items(self, **_: int) -> tuple[AttachmentRef, ...]:
        return (_image_ref(),)


class AttachmentTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_only_submit_forwards_refs_and_clears_draft(self) -> None:
        accepted = AgentEvent(
            EventKind.MESSAGE_ADDED,
            {"message": Message("user", "accepted").to_dict()},
        )
        engine = FakeEngine((accepted,))
        draft = AttachmentDraft(_Ingestor())
        await draft.add_paths((r"C:\workspace\note.txt",))
        app = WindowsTerminalApp(
            AgentController(engine),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(""))
        await app.wait_idle()

        self.assertEqual(engine.calls[0][0], "Analyze the attached input.")
        self.assertEqual(engine.attachments, (_text_ref(),))
        self.assertEqual(draft.items, ())
        self.assertNotIn(r"C:\workspace", app.state.entries[0].text)

    async def test_stream_failure_before_message_ack_keeps_draft(self) -> None:
        class FailingEngine:
            async def run(self, *_: object, **__: object):
                yield AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"})
                raise RuntimeError("persistence failed")

        draft = AttachmentDraft(_Ingestor())
        await draft.add_clipboard()
        app = WindowsTerminalApp(
            AgentController(FailingEngine()),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(""))
        await app.wait_idle()

        self.assertEqual(draft.items, (_image_ref(),))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)
        self.assertEqual(app.state.status, "error")
        self.assertIsNone(app._run_started_at)

    async def test_task_stream_failure_before_message_ack_keeps_draft(self) -> None:
        class Tasks:
            async def start(self, _: str) -> object:
                return SimpleNamespace(id="task-1")

            async def events(self, *_: object, **__: object):
                yield AgentEvent(
                    EventKind.TASK_STATUS_CHANGED,
                    {"task_id": "task-1", "status": "running"},
                )
                raise RuntimeError("message persistence failed")

        draft = AttachmentDraft(_Ingestor())
        await draft.add_clipboard()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            tasks=Tasks(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(""))
        await app.wait_idle()

        self.assertEqual(draft.items, (_image_ref(),))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)
        self.assertEqual(app.state.status, "error")
        self.assertIsNone(app._run_started_at)

    async def test_message_ack_removes_only_submitted_digests(self) -> None:
        release = asyncio.Event()

        class DelayedEngine:
            async def run(self, *_: object, **__: object):
                yield AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"})
                await release.wait()
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message("user", "accepted").to_dict()},
                )

        draft = AttachmentDraft(_Ingestor())
        await draft.add_paths((r"C:\workspace\note.txt",))
        app = WindowsTerminalApp(
            AgentController(DelayedEngine()),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(""))
        await draft.add_clipboard()
        release.set()
        await app.wait_idle()

        self.assertEqual(draft.items, (_image_ref(),))

    async def test_only_first_message_ack_can_commit_submitted_digest(self) -> None:
        first_ack = asyncio.Event()
        release_second = asyncio.Event()

        class TwoMessageEngine:
            async def run(self, *_: object, **__: object):
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message("user", "accepted").to_dict()},
                )
                first_ack.set()
                await release_second.wait()
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message("assistant", "done").to_dict()},
                )

        draft = AttachmentDraft(_Ingestor())
        await draft.add_paths((r"C:\workspace\note.txt",))
        app = WindowsTerminalApp(
            AgentController(TwoMessageEngine()),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(""))
        await first_ack.wait()
        await draft.add_paths((r"C:\workspace\note.txt",))
        release_second.set()
        await app.wait_idle()

        self.assertEqual(draft.items, (_text_ref(),))

    async def test_capability_failure_keeps_draft_and_makes_no_engine_call(self) -> None:
        engine = FakeEngine(())

        def reject(_: tuple[AttachmentRef, ...]) -> None:
            raise RuntimeError("current model does not support image input")

        draft = AttachmentDraft(_Ingestor(), validate=reject)
        await draft.add_clipboard()
        app = WindowsTerminalApp(
            AgentController(engine),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertFalse(await app.submit(""))

        self.assertEqual(engine.calls, [])
        self.assertEqual(draft.items, (_image_ref(),))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)

    async def test_file_drop_stages_without_editing_or_submitting(self) -> None:
        engine = FakeEngine(())
        draft = AttachmentDraft(_Ingestor())
        app = WindowsTerminalApp(
            AgentController(engine),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "screen shot.png"
            path.write_bytes(b"recognition only")

            await app.handle_key(f'\x1b[200~"{path}"\x1b[201~')

        self.assertEqual(app.input.text, "")
        self.assertEqual(len(draft.items), 1)
        self.assertEqual(engine.calls, [])

    async def test_empty_bracketed_ctrl_v_stages_image_without_submitting(
        self,
    ) -> None:
        engine = FakeEngine(())
        draft = AttachmentDraft(_Ingestor())
        app = WindowsTerminalApp(
            AgentController(engine),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        await app.handle_key("\x1b[200~\x1b[201~")

        self.assertEqual(draft.items, (_image_ref(),))
        self.assertEqual(app.input.text, "")
        self.assertEqual(engine.calls, [])
        self.assertIn("clipboard images staged · 1", app.state.entries[-1].text)

    async def test_attach_command_preserves_draft_on_remove_error(self) -> None:
        draft = AttachmentDraft(_Ingestor())
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            attachment_draft=draft,
            write=lambda _: None,
        )

        self.assertTrue(await app.submit(r"/attach C:\workspace\note.txt"))
        self.assertFalse(await app.submit("/attach remove unknown"))

        self.assertEqual(len(draft.items), 1)
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)

    async def test_running_task_attachment_defaults_to_durable_queue(self) -> None:
        class Tasks:
            async def queue(
                self,
                task_id: str,
                instruction: str,
                *,
                attachments: tuple[AttachmentRef, ...],
            ) -> str:
                self.seen = task_id, instruction, attachments
                return "followup-1"

        tasks = Tasks()
        draft = AttachmentDraft(_Ingestor())
        await draft.add_clipboard()
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            tasks=tasks,
            attachment_draft=draft,
            write=lambda _: None,
        )
        app.active_task_id = "task-1"
        app._run_task = asyncio.create_task(asyncio.sleep(10))
        try:
            self.assertTrue(await app.submit("inspect"))
        finally:
            app._run_task.cancel()
            await asyncio.gather(app._run_task, return_exceptions=True)

        self.assertEqual(tasks.seen, ("task-1", "inspect", (_image_ref(),)))
        self.assertEqual(draft.items, ())


if __name__ == "__main__":
    unittest.main()
