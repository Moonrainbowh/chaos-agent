from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.core.attachments import (
    MAX_MESSAGE_ATTACHMENTS,
    MAX_MESSAGE_ATTACHMENT_BYTES,
    AttachmentRef,
)
from code_agent.interfaces.attachment_input import (
    AttachmentDraft,
    attachment_path_arguments,
    dropped_file_paths,
)


def _ref(name: str, marker: str, *, image: bool = False) -> AttachmentRef:
    return AttachmentRef(
        marker * 64,
        "image/png" if image else "text/plain",
        12,
        name,
        2 if image else None,
        3 if image else None,
    )


class _Ingestor:
    def __init__(self) -> None:
        self.paths: tuple[str, ...] = ()
        self.external = False

    def ingest_paths(
        self, paths: tuple[str, ...], *, explicit_external: bool
    ) -> tuple[AttachmentRef, ...]:
        self.paths = tuple(paths)
        self.external = explicit_external
        return tuple(_ref(Path(path).name, str(index + 1)) for index, path in enumerate(paths))

    def ingest_clipboard(self) -> AttachmentRef:
        return _ref("clipboard.png", "a", image=True)


class AttachmentDraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_paths_clipboard_list_remove_and_clear(self) -> None:
        ingestor = _Ingestor()
        draft = AttachmentDraft(ingestor)

        await draft.add_paths((r"C:\work\one.txt",))
        await draft.add_clipboard()

        self.assertTrue(ingestor.external)
        self.assertEqual(len(draft.items), 2)
        self.assertNotIn(r"C:\work", "\n".join(draft.rows()))
        self.assertEqual(draft.remove("1").display_name, "one.txt")
        self.assertEqual(draft.remove("a" * 12).display_name, "clipboard.png")
        draft.clear()
        self.assertEqual(draft.rows(), ("attachments: none",))

    async def test_duplicate_and_limit_validation_is_atomic(self) -> None:
        draft = AttachmentDraft(_Ingestor())
        await draft.add_paths((r"C:\work\one.txt",))
        await draft.add_paths((r"C:\work\one.txt",))
        self.assertEqual(len(draft.items), 1)

    async def test_clipboard_batch_is_added_and_returned_together(self) -> None:
        class Multiple(_Ingestor):
            def ingest_clipboard_items(
                self, **_: int
            ) -> tuple[AttachmentRef, ...]:
                return (
                    _ref("one.png", "a", image=True),
                    _ref("two.png", "b", image=True),
                )

        draft = AttachmentDraft(Multiple())
        added = await draft.add_clipboard_items()

        self.assertEqual(added, draft.items)
        self.assertEqual(tuple(item.display_name for item in added), ("one.png", "two.png"))

    async def test_oversized_clipboard_batch_keeps_existing_draft(self) -> None:
        class TooMany(_Ingestor):
            def ingest_clipboard_items(
                self, **_: int
            ) -> tuple[AttachmentRef, ...]:
                return tuple(
                    _ref(f"{marker}.png", marker, image=True)
                    for marker in "abcdef23"
                )

        draft = AttachmentDraft(TooMany())
        await draft.add_paths((r"C:\work\one.txt",))

        with self.assertRaisesRegex(ValueError, "at most 8"):
            await draft.add_clipboard_items()

        self.assertEqual(draft.items, (_ref("one.txt", "1"),))

    async def test_clipboard_loader_receives_remaining_draft_budget(self) -> None:
        class Budgeted(_Ingestor):
            def ingest_clipboard_items(self, **budgets: int) -> tuple[AttachmentRef, ...]:
                self.budgets = budgets
                return (_ref("clipboard.png", "a", image=True),)

        ingestor = Budgeted()
        draft = AttachmentDraft(ingestor)
        await draft.add_paths((r"C:\work\one.txt",))

        await draft.add_clipboard_items()

        self.assertEqual(
            ingestor.budgets,
            {
                "max_attachments": MAX_MESSAGE_ATTACHMENTS - 1,
                "max_total_bytes": MAX_MESSAGE_ATTACHMENT_BYTES - 12,
            },
        )

    async def test_batch_api_rejects_legacy_ingestor_before_calling_it(self) -> None:
        class Legacy(_Ingestor):
            calls = 0

            def ingest_clipboard(self) -> AttachmentRef:
                self.calls += 1
                return super().ingest_clipboard()

        ingestor = Legacy()
        draft = AttachmentDraft(ingestor)

        with self.assertRaisesRegex(RuntimeError, "atomic clipboard batches"):
            await draft.add_clipboard_items()

        self.assertEqual(ingestor.calls, 0)
        self.assertEqual(draft.items, ())

    async def test_capability_validation_is_injected(self) -> None:
        def reject_images(items: tuple[AttachmentRef, ...]) -> None:
            if any(item.media_type.startswith("image/") for item in items):
                raise RuntimeError("current model does not support image input")

        draft = AttachmentDraft(_Ingestor(), validate=reject_images)
        await draft.add_clipboard()
        with self.assertRaisesRegex(RuntimeError, "does not support"):
            draft.validate()
        self.assertEqual(len(draft.items), 1)

    def test_file_drop_requires_complete_existing_supported_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "screen shot.png"
            path.write_bytes(b"not decoded at recognition time")
            pasted = f'"{path}"'
            self.assertEqual(dropped_file_paths(pasted), (str(path),))
            self.assertEqual(dropped_file_paths(f"'{path}'"), (str(path),))
            self.assertEqual(dropped_file_paths(pasted + " explain this"), ())
            self.assertEqual(dropped_file_paths("ordinary pasted text"), ())

    def test_single_quoted_windows_path_keeps_spaces_and_backslashes(self) -> None:
        value = r"'C:\Screenshots\UI error.png'"
        self.assertEqual(
            attachment_path_arguments(value),
            (r"C:\Screenshots\UI error.png",),
        )


if __name__ == "__main__":
    unittest.main()
