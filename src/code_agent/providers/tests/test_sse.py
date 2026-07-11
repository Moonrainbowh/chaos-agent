from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.errors import (  # noqa: E402
    ProviderProtocolError,
    ProviderResponseLimitError,
)
from code_agent.providers.sse import SSEDecoder, SSEEvent  # noqa: E402


class SSEDecoderTests(unittest.TestCase):
    def test_decodes_fragmented_crlf_event_and_multiline_data(self) -> None:
        raw = (
            b": heartbeat\r\n"
            b"event: response.output_text.delta\r\n"
            b"id: evt-1\r\n"
            b"data: first\r\n"
            b"data:second\r\n\r\n"
        )
        decoder = SSEDecoder(max_event_bytes=1024)

        events = []
        for byte in raw:
            events.extend(decoder.feed(bytes((byte,))))

        self.assertEqual(
            events,
            [
                SSEEvent(
                    event="response.output_text.delta",
                    data="first\nsecond",
                    id="evt-1",
                )
            ],
        )
        self.assertEqual(decoder.finalize(), [])

    def test_lf_dispatches_multiple_events_and_ignores_unknown_fields(self) -> None:
        decoder = SSEDecoder(max_event_bytes=1024)

        events = decoder.feed(
            b"retry: 20\ndata: one\n\ndata:\ndata: three\n\n"
        )

        self.assertEqual(
            events,
            [SSEEvent(data="one"), SSEEvent(data="\nthree")],
        )

    def test_finalize_dispatches_unterminated_event(self) -> None:
        decoder = SSEDecoder(max_event_bytes=1024)

        self.assertEqual(decoder.feed("data: \xe4\xbd\xa0\xe5\xa5\xbd".encode("latin1")), [])
        self.assertEqual(decoder.finalize(), [SSEEvent(data="\u4f60\u597d")])

    def test_invalid_utf8_and_bare_carriage_return_are_protocol_errors(self) -> None:
        with self.assertRaises(ProviderProtocolError):
            SSEDecoder(max_event_bytes=32).feed(b"data: \xff\n")
        with self.assertRaises(ProviderProtocolError):
            SSEDecoder(max_event_bytes=32).feed(b"data: one\rdata: two\n")

    def test_invalid_utf8_error_does_not_retain_sensitive_bytes(self) -> None:
        secret = "sse-secret-value"
        raw = b"data: api_key=" + secret.encode() + b"\xff\n"

        with self.assertRaises(ProviderProtocolError) as raised:
            SSEDecoder(max_event_bytes=128).feed(raw)

        error = raised.exception
        self.assertIsNone(error.__cause__)
        for rendered in (
            str(error),
            repr(error),
            repr(error.args),
            repr(vars(error)),
        ):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(repr(raw), rendered)

    def test_event_and_unterminated_buffer_are_bounded(self) -> None:
        decoder = SSEDecoder(max_event_bytes=16)
        decoder.feed(b"data: 1234\n")
        with self.assertRaises(ProviderResponseLimitError):
            decoder.feed(b"data: 5678\n")

        with self.assertRaises(ProviderResponseLimitError):
            SSEDecoder(max_event_bytes=8).feed(b"123456789")

    def test_decoder_rejects_feed_after_finalize(self) -> None:
        decoder = SSEDecoder(max_event_bytes=32)
        decoder.finalize()

        with self.assertRaises(ProviderProtocolError):
            decoder.feed(b"data: late\n\n")


if __name__ == "__main__":
    unittest.main()
