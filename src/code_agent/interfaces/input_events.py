from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable


MAX_PASTE_BYTES = 64 * 1024


@dataclass(frozen=True)
class InputEvent:
    """A normalized keyboard, paste, or console-control event."""

    kind: str
    value: str = ""


def paste_event(value: str) -> InputEvent:
    if not isinstance(value, str):
        raise TypeError("paste value must be a string")
    value = value.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized.encode("utf-8")) > MAX_PASTE_BYTES:
        raise ValueError("text exceeds 64 KiB (65536 UTF-8 bytes); input unchanged")
    return InputEvent("paste", normalized)


class ExitGuard:
    """Arms only after a first interrupt and expires after a short window."""

    def __init__(self, *, clock: Callable[[], float] = monotonic, window_seconds: float = 2.0) -> None:
        self._clock, self._window_seconds, self._armed_at = clock, window_seconds, None

    def interrupt(self) -> bool:
        now = self._clock()
        if self._armed_at is not None and now - self._armed_at <= self._window_seconds:
            self._armed_at = None
            return True
        self._armed_at = now
        return False

    def disarm(self) -> None:
        self._armed_at = None

    def input_received(self) -> None:
        self.disarm()
