from __future__ import annotations

from time import monotonic


_MAX_PHASE_DURATION_MS = 86_400_000


def phase_started_at() -> float:
    """Return a monotonic timestamp for one engine phase."""
    return monotonic()


def phase_duration_ms(started_at: float) -> int:
    """Return a bounded non-negative elapsed duration for durable events."""
    return min(
        _MAX_PHASE_DURATION_MS,
        max(0, int((monotonic() - started_at) * 1_000)),
    )
