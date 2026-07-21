from __future__ import annotations

import time
from collections.abc import Callable

from code_agent.context.tokens import estimate_tokens


class TokenRateTracker:
    """Track estimated live and provider-calibrated model output throughput."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        """Clear output and timing for a new model turn."""
        self._text = ""
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._output_tokens: int | None = None

    def observe_text(self, text: str) -> None:
        """Add a streamed text delta and start timing at the first visible output."""
        if not text:
            return
        if self._started_at is None:
            self._started_at = self._clock()
            self._output_tokens = None
        self._text += text

    def calibrate(self, output_tokens: int) -> None:
        """Replace the estimate with provider usage when output timing exists."""
        if self._started_at is None:
            return
        self._output_tokens = output_tokens
        self._finished_at = self._clock()

    def rate(self, now: float | None = None) -> float | None:
        """Return average output tokens per second since the first text delta."""
        if self._started_at is None:
            return None
        end = self._finished_at if self._finished_at is not None else (
            self._clock() if now is None else now
        )
        elapsed = end - self._started_at
        tokens = (
            self._output_tokens
            if self._output_tokens is not None
            else estimate_tokens(self._text)
        )
        if elapsed <= 0 or tokens <= 0:
            return None
        return tokens / elapsed

