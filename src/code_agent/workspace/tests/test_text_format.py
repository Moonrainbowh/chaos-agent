from __future__ import annotations

import codecs
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace._text_codec import (
    TextCodecError,
    TextFileFormat,
    decode_text_bytes,
    encode_existing_text,
)
from code_agent.workspace.edits import EditPlan, WorkspaceEditor
from code_agent.workspace.errors import BinaryFileError
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.rewind_state import prepare_edit_state


class TextFormatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)
        self.editor = WorkspaceEditor(self.guard)
        self.files = WorkspaceFiles(
            self.guard, IgnoreRules.from_workspace(self.root)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()


class DecodeMetadataTests(TextFormatTestCase):
    def test_unicode_bom_matrix_round_trips_with_metadata(self) -> None:
        text = "alpha\r\nbeta\r\n"
        cases = (
            ("utf8", codecs.BOM_UTF8, "utf-8"),
            ("utf16le", codecs.BOM_UTF16_LE, "utf-16-le"),
            ("utf16be", codecs.BOM_UTF16_BE, "utf-16-be"),
            ("utf32le", codecs.BOM_UTF32_LE, "utf-32-le"),
            ("utf32be", codecs.BOM_UTF32_BE, "utf-32-be"),
        )
        for name, bom, codec in cases:
            with self.subTest(codec=codec):
                raw = bom + text.encode(codec)
                (self.root / name).write_bytes(raw)

                document = self.files.read_text(name)
                decoded = decode_text_bytes(raw)

                self.assertEqual(document.text, text)
                self.assertEqual(document.text_format.encoding, codec)
                self.assertTrue(document.text_format.bom)
                self.assertEqual(document.text_format.newline, "crlf")
                self.assertEqual(encode_existing_text(
                    decoded.text, decoded.format
                ).data, raw)

    def test_utf32_bom_is_checked_before_utf16(self) -> None:
        raw = codecs.BOM_UTF32_LE + "wide".encode("utf-32-le")

        decoded = decode_text_bytes(raw)

        self.assertEqual(decoded.text, "wide")
        self.assertEqual(decoded.format.encoding, "utf-32-le")

    def test_auto_is_strict_utf8_without_a_bom(self) -> None:
        (self.root / "bad.txt").write_bytes(b"\xff")
        (self.root / "nul.txt").write_bytes(b"text\0data")

        for name in ("bad.txt", "nul.txt"):
            with self.subTest(name=name), self.assertRaises(BinaryFileError):
                self.files.read_text(name)


class NewlineAndWriteTests(TextFormatTestCase):
    def test_full_write_preserves_each_consistent_newline_style(self) -> None:
        for name, newline in (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r")):
            with self.subTest(newline=name):
                target = self.root / f"{name}.txt"
                target.write_bytes(f"old{newline}value{newline}".encode())
                expected = f"new{newline}value{newline}"

                plan = self.editor.plan_write(target.name, "new\nvalue\n")
                self.editor.apply(plan)

                self.assertEqual(plan.after_text, expected)
                self.assertEqual(target.read_bytes(), expected.encode())
                self.assertEqual(plan.text_format.newline, name)

    def test_full_write_does_not_normalize_a_mixed_source(self) -> None:
        target = self.root / "mixed.txt"
        target.write_bytes(b"old\r\nmiddle\nlast\r")
        replacement = "new\nmiddle\r\nlast\r"

        plan = self.editor.plan_write(target.name, replacement)
        self.editor.apply(plan)

        self.assertEqual(plan.after_text, replacement)
        self.assertEqual(target.read_bytes(), replacement.encode())
        self.assertEqual(plan.text_format.newline, "mixed")

    def test_replace_preserves_bom_and_untouched_mixed_newlines(self) -> None:
        target = self.root / "replace.txt"
        target.write_bytes(codecs.BOM_UTF8 + b"top\r\nold\nbottom\r")

        plan = self.editor.plan_replace(target.name, "old", "new")
        self.editor.apply(plan)

        expected = codecs.BOM_UTF8 + b"top\r\nnew\nbottom\r"
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(plan.text_format.newline, "mixed")
        self.assertTrue(plan.text_format.bom)

    def test_replace_preserves_exact_unicode_bom_bytes(self) -> None:
        cases = (
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
            (codecs.BOM_UTF32_LE, "utf-32-le"),
            (codecs.BOM_UTF32_BE, "utf-32-be"),
        )
        for index, (bom, codec) in enumerate(cases):
            with self.subTest(codec=codec):
                target = self.root / f"replace-bom-{index}.txt"
                target.write_bytes(bom + "old\r\ntail\r\n".encode(codec))

                plan = self.editor.plan_replace(target.name, "old", "new")
                expected = bom + "new\r\ntail\r\n".encode(codec)

                self.assertEqual(plan.after_bytes, expected)
                self.editor.apply(plan)
                self.assertEqual(target.read_bytes(), expected)

    def test_new_file_defaults_to_utf8_without_bom(self) -> None:
        plan = self.editor.plan_write("new.txt", "汉字\n")
        self.editor.apply(plan)

        self.assertEqual((self.root / "new.txt").read_bytes(), "汉字\n".encode())
        self.assertEqual(plan.text_format.encoding, "utf-8")
        self.assertFalse(plan.text_format.bom)


class WindowsCodePageTests(TextFormatTestCase):
    def test_explicit_ansi_and_oem_round_trip_exactly(self) -> None:
        cases = (
            ("windows-ansi", 1252, "cp1252", "café", "résumé"),
            ("windows-oem", 437, "cp437", "café", "touché"),
        )
        for request, page, codec, before, after in cases:
            with self.subTest(request=request), patch(
                "code_agent.workspace._text_codec._windows_code_page",
                return_value=(page, codec),
            ):
                target = self.root / f"{request}.txt"
                target.write_bytes((before + "\r\n").encode(codec))

                document = self.files.read_text(target.name, encoding=request)
                plan = self.editor.plan_replace(
                    target.name, before, after, encoding=request
                )
                self.editor.apply(plan)

                self.assertEqual(document.text_format.encoding, request)
                self.assertEqual(document.text_format.code_page, page)
                self.assertEqual(target.read_bytes(), (after + "\r\n").encode(codec))

    def test_invalid_or_unrepresentable_ansi_fails_before_apply(self) -> None:
        target = self.root / "ansi.txt"
        target.write_bytes(b"plain")
        with patch(
            "code_agent.workspace._text_codec._windows_code_page",
            return_value=(1252, "cp1252"),
        ):
            with self.assertRaises(TextCodecError):
                self.editor.plan_replace(
                    target.name, "plain", "汉", encoding="windows-ansi"
                )
            (self.root / "invalid.txt").write_bytes(b"\x81")
            with self.assertRaises(BinaryFileError):
                self.files.read_text("invalid.txt", encoding="windows-ansi")

        self.assertEqual(target.read_bytes(), b"plain")


class HashAndPlanValidationTests(TextFormatTestCase):
    def test_after_hash_uses_exact_bom_and_encoded_bytes(self) -> None:
        cases = (
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
            (codecs.BOM_UTF32_LE, "utf-32-le"),
            (codecs.BOM_UTF32_BE, "utf-32-be"),
        )
        for index, (bom, codec) in enumerate(cases):
            with self.subTest(codec=codec):
                target = self.root / f"hash-{index}.txt"
                target.write_bytes(bom + "old\r\n".encode(codec))

                plan = self.editor.plan_write(target.name, "new\n")
                prepared = prepare_edit_state(self.editor, plan)
                self.editor.apply(plan)

                self.assertIsInstance(plan.after_bytes, bytes)
                expected_hash = hashlib.sha256(plan.after_bytes).hexdigest()
                self.assertEqual(prepared.after.sha256, expected_hash)
                self.assertEqual(prepared.after.size, len(plan.after_bytes))
                self.assertEqual(target.read_bytes(), plan.after_bytes)

    def test_edit_plan_rejects_malformed_explicit_bytes(self) -> None:
        text_format = TextFileFormat("utf-8", True, "lf")

        with self.assertRaisesRegex(ValueError, "must agree"):
            EditPlan("x.txt", None, "x\n", "", False, b"x\n", text_format)
        wrong_newline = TextFileFormat("utf-8", False, "crlf")
        with self.assertRaisesRegex(ValueError, "must agree"):
            EditPlan("x.txt", None, "x\n", "", False, b"x\n", wrong_newline)

    def test_legacy_positional_plan_defaults_to_utf8_no_bom(self) -> None:
        plan = EditPlan("x.txt", None, "x\n", "", False)

        self.assertEqual(plan.after_bytes, b"x\n")
        self.assertEqual(plan.text_format, TextFileFormat("utf-8", False, "lf"))


if __name__ == "__main__":
    unittest.main()
