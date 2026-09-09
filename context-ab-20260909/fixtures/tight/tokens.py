from __future__ import annotations

from typing import Iterator


def estimate_tokens(text: str) -> int:
    """Return a deterministic conservative estimate for mixed UTF-8 text."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    ascii_count = 0
    multibyte_count = 0
    for cluster in _clusters(text):
        if len(cluster) == 1 and ord(cluster) < 128:
            ascii_count += 1
        else:
            multibyte_count += len(_cluster_bytes(cluster))
    return _ceil_div(ascii_count, 4) + _ceil_div(multibyte_count, 3)


def truncate_to_tokens(text: str, token_budget: int) -> str:
    """Return the longest character-safe prefix within ``token_budget``."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("token_budget must be an integer")
    if token_budget < 0:
        raise ValueError("token_budget must not be negative")
    if token_budget == 0 or not text:
        return ""
    if estimate_tokens(text) <= token_budget:
        return text

    clusters = tuple(_clusters(text))
    low, high = 0, len(clusters)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_tokens("".join(clusters[:midpoint])) <= token_budget:
            low = midpoint
        else:
            high = midpoint - 1
    return "".join(clusters[:low])


def _clusters(text: str) -> Iterator[str]:
    index = 0
    while index < len(text):
        current = ord(text[index])
        if 0xD800 <= current <= 0xDBFF and index + 1 < len(text):
            following = ord(text[index + 1])
            if 0xDC00 <= following <= 0xDFFF:
                yield text[index : index + 2]
                index += 2
                continue
        yield text[index]
        index += 1


def _cluster_bytes(cluster: str) -> bytes:
    if len(cluster) == 2:
        high, low = map(ord, cluster)
        codepoint = 0x10000 + ((high - 0xD800) << 10) + low - 0xDC00
        return chr(codepoint).encode("utf-8")
    return cluster.encode("utf-8", errors="surrogatepass")


def _ceil_div(value: int, divisor: int) -> int:
    return value // divisor
