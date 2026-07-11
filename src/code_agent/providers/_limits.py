from __future__ import annotations

from .errors import ProviderResponseLimitError


def ensure_utf8_limit(value: str, limit: int) -> None:
    if len(value.encode("utf-8")) > limit:
        raise ProviderResponseLimitError("Tool arguments exceeded their byte limit")


class ArgumentBuffer:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: list[str] = []
        self._bytes = 0

    @property
    def is_empty(self) -> bool:
        return not self._chunks

    def append(self, value: str) -> None:
        size = len(value.encode("utf-8"))
        if self._bytes + size > self._limit:
            raise ProviderResponseLimitError(
                "Tool arguments exceeded their byte limit"
            )
        self._chunks.append(value)
        self._bytes += size

    def replace(self, value: str) -> None:
        ensure_utf8_limit(value, self._limit)
        self._chunks = [value]
        self._bytes = len(value.encode("utf-8"))

    def text(self) -> str:
        return "".join(self._chunks)


class ToolBudget:
    def __init__(self, max_calls: int, max_argument_bytes: int) -> None:
        self._max_calls = max_calls
        self._max_argument_bytes = max_argument_bytes
        self._calls = 0

    @property
    def max_argument_bytes(self) -> int:
        return self._max_argument_bytes

    def new_arguments(self) -> ArgumentBuffer:
        if self._calls >= self._max_calls:
            raise ProviderResponseLimitError("Tool call count exceeded its limit")
        self._calls += 1
        return ArgumentBuffer(self._max_argument_bytes)
