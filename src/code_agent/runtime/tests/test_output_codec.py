from __future__ import annotations

import base64
import codecs
import unittest
from unittest.mock import patch

from code_agent.runtime.output_codec import (
    OutputDecodeStatus,
    OutputEncoding,
    decode_output,
)


class OutputCodecTests(unittest.TestCase):
    def test_default_is_strict_utf8_without_duplicate_base64(self) -> None:
        decoded = decode_output("中文✓".encode("utf-8"))

        self.assertEqual(decoded.text, "中文✓")
        self.assertIs(decoded.encoding, OutputEncoding.UTF_8)
        self.assertIs(decoded.status, OutputDecodeStatus.DECODED)
        self.assertIsNone(decoded.base64_data)

    def test_bom_selects_utf8_or_utf16_and_strips_marker(self) -> None:
        cases = (
            (codecs.BOM_UTF8 + "中".encode("utf-8"), OutputEncoding.UTF_8),
            (
                codecs.BOM_UTF16_LE + "中".encode("utf-16-le"),
                OutputEncoding.UTF_16_LE,
            ),
            (
                codecs.BOM_UTF16_BE + "中".encode("utf-16-be"),
                OutputEncoding.UTF_16_BE,
            ),
        )

        for raw, expected in cases:
            with self.subTest(expected=expected):
                decoded = decode_output(raw, OutputEncoding.WINDOWS_ANSI)
                self.assertEqual(decoded.text, "中")
                self.assertIs(decoded.encoding, expected)
                self.assertIs(decoded.status, OutputDecodeStatus.DECODED)

    def test_explicit_utf16_without_bom_is_supported(self) -> None:
        little = decode_output(
            "A中".encode("utf-16-le"), OutputEncoding.UTF_16_LE
        )
        big = decode_output(
            "A中".encode("utf-16-be"), OutputEncoding.UTF_16_BE
        )

        self.assertEqual(little.text, "A中")
        self.assertEqual(big.text, "A中")

    def test_windows_code_pages_are_resolved_strictly(self) -> None:
        with patch(
            "code_agent.runtime.output_codec._windows_code_page",
            side_effect=lambda *, oem: 437 if oem else 1252,
        ):
            ansi = decode_output(b"caf\xe9", OutputEncoding.WINDOWS_ANSI)
            oem = decode_output(b"caf\x82", OutputEncoding.WINDOWS_OEM)

        self.assertEqual((ansi.text, ansi.code_page), ("café", 1252))
        self.assertEqual((oem.text, oem.code_page), ("café", 437))

    def test_failed_windows_decode_keeps_code_page_and_complete_bytes(self) -> None:
        raw = b"prefix\x81"
        cases = (
            (OutputEncoding.WINDOWS_ANSI, 1252),
            (OutputEncoding.WINDOWS_OEM, 932),
        )

        for encoding, page in cases:
            with self.subTest(encoding=encoding), patch(
                "code_agent.runtime.output_codec._windows_code_page",
                return_value=page,
            ):
                decoded = decode_output(raw, encoding)

                self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)
                self.assertEqual(decoded.code_page, page)
                self.assertEqual(
                    decoded.base64_data,
                    base64.b64encode(raw).decode("ascii"),
                )

    def test_unavailable_windows_codec_still_reports_resolved_page(self) -> None:
        with patch(
            "code_agent.runtime.output_codec._windows_code_page",
            return_value=99_999,
        ):
            decoded = decode_output(b"raw", OutputEncoding.WINDOWS_ANSI)

        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)
        self.assertEqual(decoded.code_page, 99_999)
        self.assertEqual(decoded.base64_data, "cmF3")

    def test_invalid_utf8_is_unknown_and_preserves_complete_bytes(self) -> None:
        raw = b"prefix\x80\x81suffix"

        decoded = decode_output(raw)

        self.assertIsNone(decoded.text)
        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)
        self.assertEqual(
            decoded.base64_data,
            base64.b64encode(raw).decode("ascii"),
        )

    def test_truncated_multibyte_tail_has_specific_status(self) -> None:
        raw = "中".encode("utf-8")[:2]

        decoded = decode_output(raw, truncated=True)

        self.assertIs(decoded.status, OutputDecodeStatus.INCOMPLETE_TAIL)
        self.assertEqual(decoded.base64_data, base64.b64encode(raw).decode("ascii"))

    def test_truncation_does_not_relabel_an_earlier_invalid_byte(self) -> None:
        decoded = decode_output(b"\x80valid", truncated=True)

        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)

    def test_truncation_does_not_relabel_an_illegal_utf8_tail(self) -> None:
        decoded = decode_output(b"valid\x80", truncated=True)

        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)

    def test_utf16_odd_tail_is_incomplete_only_when_capture_was_truncated(self) -> None:
        raw = "A中".encode("utf-16-le")[:-1]

        truncated = decode_output(
            raw,
            OutputEncoding.UTF_16_LE,
            truncated=True,
        )
        complete_capture = decode_output(raw, OutputEncoding.UTF_16_LE)

        self.assertIs(truncated.status, OutputDecodeStatus.INCOMPLETE_TAIL)
        self.assertIs(
            complete_capture.status,
            OutputDecodeStatus.UNKNOWN_OR_MIXED,
        )

    def test_utf16_illegal_surrogate_tail_is_not_incomplete(self) -> None:
        raw = "A".encode("utf-16-le") + b"\x00\xdc"

        decoded = decode_output(
            raw,
            OutputEncoding.UTF_16_LE,
            truncated=True,
        )

        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)

    def test_utf32_bom_is_not_misread_as_utf16(self) -> None:
        raw = codecs.BOM_UTF32_LE + "A".encode("utf-32-le")

        decoded = decode_output(raw)

        self.assertIs(decoded.status, OutputDecodeStatus.UNKNOWN_OR_MIXED)
        self.assertEqual(decoded.base64_data, base64.b64encode(raw).decode("ascii"))


if __name__ == "__main__":
    unittest.main()
