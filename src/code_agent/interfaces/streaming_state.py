from __future__ import annotations

from .terminal_display import graphemes, safe_text


PARTIAL_TEXT_LIMIT = 4_096
PARTIAL_TRUNCATION_MARKER = "\n… [本地截断]"


class DraftBuffer:
    """Own raw provider deltas while exposing a safe revisioned projection."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.revision = 0

    @property
    def raw_text(self) -> str:
        return "".join(self._parts)

    @property
    def safe_text(self) -> str:
        return safe_text(self.raw_text)

    @property
    def has_text(self) -> bool:
        return bool(self._parts)

    def append(self, value: str) -> None:
        self._parts.append(value)
        self.revision += 1

    def clear(self) -> None:
        if self._parts:
            self._parts = []
            self.revision += 1

    def take_partial(self) -> str:
        value = _bounded_partial(self.safe_text)
        self.clear()
        return value


def _bounded_partial(value: str) -> str:
    if len(value) <= PARTIAL_TEXT_LIMIT:
        return value
    budget = PARTIAL_TEXT_LIMIT - len(PARTIAL_TRUNCATION_MARKER)
    result: list[str] = []
    used = 0
    for cluster in graphemes(value):
        if used + len(cluster) > budget:
            break
        result.append(cluster)
        used += len(cluster)
    return "".join(result).rstrip() + PARTIAL_TRUNCATION_MARKER
