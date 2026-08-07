from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.attachments.errors import (  # noqa: E402
    AttachmentError,
    AttachmentIntegrityError,
)
from code_agent.attachments.ingest import AttachmentIngestor  # noqa: E402
from code_agent.attachments.store import AttachmentStore  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


def image_bytes(*, metadata: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (3, 2), (20, 40, 60))
    options = {"comment": b"private metadata"} if metadata else {}
    image.save(output, "PNG", **options)
    return output.getvalue()


class AttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = AttachmentStore(self.root / "state" / "attachments")
        self.ingestor = AttachmentIngestor(
            self.store,
            workspace_guard=WorkspacePathGuard(self.workspace),
            ignore_rules=IgnoreRules(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_image_is_decoded_normalized_and_content_addressed(self) -> None:
        source = self.workspace / "capture.png"
        source.write_bytes(image_bytes(metadata=True))

        first = self.ingestor.ingest_path(source)
        second = self.ingestor.ingest_path(source)
        stored = self.store.read(first)

        self.assertEqual(first, second)
        self.assertEqual(first.media_type, "image/png")
        self.assertEqual((first.width, first.height), (3, 2))
        self.assertNotIn(b"private metadata", stored)
        with Image.open(io.BytesIO(stored)) as normalized:
            normalized.verify()
        self.assertNotIn("path", first.to_dict())

    def test_utf8_text_is_normalized_and_ignored_path_is_rejected(self) -> None:
        text = self.workspace / "note.md"
        text.write_bytes(b"\xef\xbb\xbfhello")
        reference = self.ingestor.ingest_path(text)
        self.assertEqual(self.store.read(reference), b"hello")

        ignored = AttachmentIngestor(
            self.store,
            workspace_guard=WorkspacePathGuard(self.workspace),
            ignore_rules=IgnoreRules.from_workspace(self.workspace),
        )
        (self.workspace / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        (self.workspace / "secret.txt").write_text("hidden", encoding="utf-8")
        ignored = AttachmentIngestor(
            self.store,
            workspace_guard=WorkspacePathGuard(self.workspace),
            ignore_rules=IgnoreRules.from_workspace(self.workspace),
        )
        with self.assertRaises(AttachmentError):
            ignored.ingest_path(self.workspace / "secret.txt")

    def test_external_path_must_be_explicit_and_non_sensitive(self) -> None:
        external = self.root / "outside.txt"
        external.write_text("allowed", encoding="utf-8")
        with self.assertRaises(AttachmentError):
            self.ingestor.ingest_path(external)
        reference = self.ingestor.ingest_path(external, explicit_external=True)
        self.assertEqual(self.store.read(reference), b"allowed")

        sensitive = self.root / ".env"
        sensitive.write_text("TOKEN=value", encoding="utf-8")
        with self.assertRaises(AttachmentError):
            self.ingestor.ingest_path(sensitive, explicit_external=True)

    def test_product_config_directory_is_never_attachable(self) -> None:
        local = self.root / "local"
        config = local / "chaos-agent" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("api_key='secret'", encoding="utf-8")
        with patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=True):
            with self.assertRaises(AttachmentError):
                self.ingestor.ingest_path(config, explicit_external=True)

    def test_clipboard_image_is_validated_before_png_encoding(self) -> None:
        cases = (
            ("animated", (1, 1), True),
            ("zero width", (0, 1), False),
            ("too many pixels", (5_001, 5_001), False),
        )
        for label, size, animated in cases:
            with self.subTest(label=label):
                image = Image.new("RGB", (1, 1))
                image._size = size
                image.is_animated = animated
                image.n_frames = 2 if animated else 1
                with (
                    patch("PIL.ImageGrab.grabclipboard", return_value=image),
                    patch.object(image, "save") as save,
                    self.assertRaises(AttachmentError),
                ):
                    self.ingestor.ingest_clipboard()
                save.assert_not_called()

    def test_clipboard_bitmap_returns_one_reference_batch(self) -> None:
        image = Image.new("RGB", (2, 3), (10, 20, 30))
        with patch("PIL.ImageGrab.grabclipboard", return_value=image):
            references = self.ingestor.ingest_clipboard_items()

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].display_name, "clipboard.png")
        self.assertEqual((references[0].width, references[0].height), (2, 3))

    def test_clipboard_bitmap_budget_failure_does_not_publish(self) -> None:
        image = Image.new("RGB", (2, 3), (10, 20, 30))
        with (
            patch("PIL.ImageGrab.grabclipboard", return_value=image),
            self.assertRaisesRegex(AttachmentError, "total byte limit"),
        ):
            self.ingestor.ingest_clipboard_items(max_total_bytes=1)

        self.assertEqual(tuple(self.store.root.rglob("*.blob")), ())

    def test_clipboard_file_list_ingests_all_images(self) -> None:
        paths = (self.workspace / "one.png", self.workspace / "two.jpg")
        for index, path in enumerate(paths):
            Image.new("RGB", (index + 1, 2), (10, 20, 30)).save(path)

        with patch(
            "PIL.ImageGrab.grabclipboard",
            return_value=[str(path) for path in paths],
        ):
            references = self.ingestor.ingest_clipboard_items()

        self.assertEqual(
            tuple(item.display_name for item in references),
            ("one.png", "two.png"),
        )
        self.assertEqual(
            tuple((item.width, item.height) for item in references),
            ((1, 2), (2, 2)),
        )

    def test_clipboard_mixed_file_list_fails_before_publishing(self) -> None:
        image = self.workspace / "one.png"
        text = self.workspace / "note.txt"
        Image.new("RGB", (1, 1), (10, 20, 30)).save(image)
        text.write_text("not an image", encoding="utf-8")

        with (
            patch(
                "PIL.ImageGrab.grabclipboard",
                return_value=[str(image), str(text)],
            ),
            self.assertRaisesRegex(AttachmentError, "supported images"),
        ):
            self.ingestor.ingest_clipboard_items()

        self.assertEqual(tuple(self.store.root.rglob("*.blob")), ())

    def test_clipboard_corrupt_image_batch_does_not_publish_first_image(self) -> None:
        valid = self.workspace / "valid.png"
        corrupt = self.workspace / "corrupt.png"
        Image.new("RGB", (1, 1), (10, 20, 30)).save(valid)
        corrupt.write_bytes(b"not an image")

        with (
            patch(
                "PIL.ImageGrab.grabclipboard",
                return_value=[str(valid), str(corrupt)],
            ),
            self.assertRaisesRegex(AttachmentError, "valid supported image"),
        ):
            self.ingestor.ingest_clipboard_items()

        self.assertEqual(tuple(self.store.root.rglob("*.blob")), ())

    def test_clipboard_file_list_respects_attachment_count_limit(self) -> None:
        image = self.workspace / "one.png"
        Image.new("RGB", (1, 1), (10, 20, 30)).save(image)
        with (
            patch("PIL.ImageGrab.grabclipboard", return_value=[str(image)] * 9),
            self.assertRaisesRegex(AttachmentError, "too many attachments"),
        ):
            self.ingestor.ingest_clipboard_items()

        self.assertEqual(tuple(self.store.root.rglob("*.blob")), ())

    def test_corrupted_blob_fails_integrity_check(self) -> None:
        reference = self.ingestor.ingest_bytes(
            b"hello", display_name="note.txt", media_type="text"
        )
        blob = (
            self.store.root
            / reference.sha256[:2]
            / f"{reference.sha256}.blob"
        )
        blob.write_bytes(b"other")
        with self.assertRaises(AttachmentIntegrityError):
            self.store.read(reference)

    def test_failed_batch_does_not_publish_earlier_staged_blobs(self) -> None:
        valid = self.workspace / "first.txt"
        invalid = self.workspace / "second.txt"
        valid.write_text("valid", encoding="utf-8")
        invalid.write_bytes(b"binary\0payload")

        with self.assertRaises(AttachmentError):
            self.ingestor.ingest_paths((valid, invalid))

        self.assertEqual(tuple(self.store.root.rglob("*.blob")), ())


if __name__ == "__main__":
    unittest.main()
