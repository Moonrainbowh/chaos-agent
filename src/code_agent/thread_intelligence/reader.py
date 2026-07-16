from __future__ import annotations

from .index import BoundedThreadIndex
from .models import EntryRelation, SourceAnchor, ThreadRead


class ThreadReader:
    def __init__(self, index: BoundedThreadIndex) -> None:
        if not isinstance(index, BoundedThreadIndex):
            raise TypeError("index must be BoundedThreadIndex")
        self._index = index

    def read_thread(self, anchor: SourceAnchor, *, max_followups: int = 100) -> ThreadRead:
        if isinstance(max_followups, bool) or max_followups <= 0 or max_followups > 1_000:
            raise ValueError("max_followups must be between 1 and 1000")
        primary = self._index.get(anchor)
        later = self._index.entries_after(anchor, limit=max_followups)
        revisions = tuple(
            entry for entry in later
            if entry.related_id == anchor.stable_id
            and entry.relation in {EntryRelation.SUPERSEDES, EntryRelation.REVERTS}
        )
        outcomes = ()
        if primary.tool_call_id is not None:
            outcomes = tuple(
                entry for entry in later
                if entry.tool_call_id == primary.tool_call_id
                and entry.tool_is_error is not None
            )
        return ThreadRead(primary, revisions, outcomes, bool(revisions))
