from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .errors import ProviderProtocolError, ProviderResponseLimitError


@dataclass(frozen=True)
class SSEEvent:
    data: str
    event: Optional[str] = None
    id: Optional[str] = None


class SSEDecoder:
    """Incrementally decode a bounded UTF-8 SSE byte stream."""

    def __init__(self, max_event_bytes: int) -> None:
        if isinstance(max_event_bytes, bool) or not isinstance(max_event_bytes, int):
            raise TypeError("max_event_bytes must be an integer")
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")
        self._limit = max_event_bytes
        self._buffer = bytearray()
        self._event_name: Optional[str] = None
        self._event_id: Optional[str] = None
        self._data: list[str] = []
        self._has_data = False
        self._event_bytes = 0
        self._finalized = False

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        if self._finalized:
            raise ProviderProtocolError("SSE decoder is already finalized")
        if not isinstance(chunk, bytes):
            raise ProviderProtocolError("SSE chunks must be bytes")

        events: list[SSEEvent] = []
        offset = 0
        while offset < len(chunk):
            newline = chunk.find(b"\n", offset)
            if newline < 0:
                self._append_buffer(chunk[offset:])
                break
            self._append_buffer(chunk[offset:newline])
            line = bytes(self._buffer)
            self._buffer.clear()
            if line.endswith(b"\r"):
                line = line[:-1]
            if b"\r" in line:
                raise ProviderProtocolError("Malformed SSE line ending")
            self._process_line(line, events)
            offset = newline + 1
        return events

    def finalize(self) -> list[SSEEvent]:
        if self._finalized:
            return []
        self._finalized = True
        events: list[SSEEvent] = []
        if self._buffer:
            line = bytes(self._buffer)
            self._buffer.clear()
            if b"\r" in line:
                raise ProviderProtocolError("Malformed SSE line ending")
            self._process_line(line, events)
        event = self._dispatch()
        if event is not None:
            events.append(event)
        return events

    def _append_buffer(self, value: bytes) -> None:
        self._buffer.extend(value)
        if len(self._buffer) > self._limit:
            raise ProviderResponseLimitError("SSE line buffer exceeded its limit")

    def _process_line(self, raw_line: bytes, events: list[SSEEvent]) -> None:
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ProviderProtocolError("SSE stream contains invalid UTF-8") from None
        if not line:
            event = self._dispatch()
            if event is not None:
                events.append(event)
            return
        if line.startswith(":"):
            return

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field not in {"data", "event", "id"}:
            return
        self._event_bytes += len(raw_line) + 1
        if self._event_bytes > self._limit:
            raise ProviderResponseLimitError("SSE event exceeded its limit")
        if field == "data":
            self._data.append(value)
            self._has_data = True
        elif field == "event":
            self._event_name = value or None
        elif "\x00" not in value:
            self._event_id = value

    def _dispatch(self) -> Optional[SSEEvent]:
        if not self._has_data:
            self._reset_event()
            return None
        event = SSEEvent(
            data="\n".join(self._data),
            event=self._event_name,
            id=self._event_id,
        )
        self._reset_event()
        return event

    def _reset_event(self) -> None:
        self._event_name = None
        self._event_id = None
        self._data.clear()
        self._has_data = False
        self._event_bytes = 0
