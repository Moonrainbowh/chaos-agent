from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable

from .models import SearchHit, SourceAnchor, ThreadEntry


class ThreadAccessError(PermissionError):
    pass


class BoundedThreadIndex:
    """In-memory bounded index restricted to an explicit thread tree."""

    def __init__(self, root_thread_id: str, authorized_threads: Iterable[str] = (), *, capacity: int = 2_000) -> None:
        if not isinstance(root_thread_id, str) or not root_thread_id.strip():
            raise ValueError("root_thread_id must be non-blank")
        threads = {root_thread_id, *authorized_threads}
        if any(not isinstance(value, str) or not value.strip() for value in threads):
            raise ValueError("authorized thread ids must be non-blank")
        if isinstance(capacity, bool) or capacity <= 0 or capacity > 100_000:
            raise ValueError("capacity must be between 1 and 100000")
        self._authorized = frozenset(threads)
        self._capacity = capacity
        self._entries: deque[ThreadEntry] = deque()
        self._by_id: dict[str, ThreadEntry] = {}

    @property
    def authorized_threads(self) -> frozenset[str]:
        return self._authorized

    def add(self, entry: ThreadEntry) -> None:
        if not isinstance(entry, ThreadEntry):
            raise TypeError("entry must be ThreadEntry")
        self._authorize(entry.anchor.thread_id)
        prior = self._by_id.get(entry.anchor.stable_id)
        if prior is not None:
            if prior != entry:
                raise ValueError("stable_id already refers to different content")
            return
        while len(self._entries) >= self._capacity:
            evicted = self._entries.popleft()
            self._by_id.pop(evicted.anchor.stable_id, None)
        self._entries.append(entry)
        self._by_id[entry.anchor.stable_id] = entry

    def get(self, anchor: SourceAnchor) -> ThreadEntry:
        if not isinstance(anchor, SourceAnchor):
            raise TypeError("anchor must be SourceAnchor")
        self._authorize(anchor.thread_id)
        entry = self._by_id.get(anchor.stable_id)
        if entry is None or entry.anchor != anchor:
            raise KeyError("source anchor is not indexed")
        return entry

    def search(self, query: str, *, thread_ids: Iterable[str] | None = None, limit: int = 20) -> tuple[SearchHit, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("query must be non-blank bounded text")
        if isinstance(limit, bool) or limit <= 0 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        selected = self._authorized if thread_ids is None else frozenset(thread_ids)
        for thread_id in selected:
            self._authorize(thread_id)
        terms = tuple(dict.fromkeys(re.findall(r"\w+", query.casefold())))
        hits: list[SearchHit] = []
        for entry in self._entries:
            if entry.anchor.thread_id not in selected:
                continue
            haystack = entry.text.casefold()
            score = sum(2 if term == haystack else 1 for term in terms if term in haystack)
            if score:
                hits.append(SearchHit(entry, score))
        hits.sort(key=lambda hit: (-hit.score, -hit.entry.anchor.sequence, hit.entry.anchor.stable_id))
        return tuple(hits[:limit])

    def entries_after(self, anchor: SourceAnchor, *, limit: int = 100) -> tuple[ThreadEntry, ...]:
        self._authorize(anchor.thread_id)
        values = (
            entry for entry in self._entries
            if entry.anchor.thread_id == anchor.thread_id and entry.anchor.sequence > anchor.sequence
        )
        return tuple(sorted(values, key=lambda item: item.anchor.sequence)[:limit])

    def _authorize(self, thread_id: str) -> None:
        if thread_id not in self._authorized:
            raise ThreadAccessError("thread is outside the authorized thread tree")
