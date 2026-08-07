from __future__ import annotations


def batch_limit(requested: int | None, configured: int, label: str) -> int:
    if requested is None:
        return configured
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise TypeError(f"{label} must be an integer")
    if requested < 0:
        raise ValueError(f"{label} must not be negative")
    return min(requested, configured)
