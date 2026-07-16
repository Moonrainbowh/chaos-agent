from __future__ import annotations

import hashlib
import unittest

from code_agent.thread_intelligence.index import BoundedThreadIndex, ThreadAccessError
from code_agent.thread_intelligence.models import (
    EntryRelation,
    SourceAnchor,
    SourceKind,
    ThreadEntry,
)
from code_agent.thread_intelligence.reader import ThreadReader


def _entry(
    thread: str,
    sequence: int,
    text: str,
    *,
    relation: EntryRelation = EntryRelation.NONE,
    related_id: str | None = None,
    call_id: str | None = None,
    is_error: bool | None = None,
) -> ThreadEntry:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor = SourceAnchor(thread, SourceKind.EVENT, sequence, f"{thread}:event:{sequence}", digest)
    return ThreadEntry(anchor, text, relation, related_id, call_id, is_error)


class BoundedThreadIndexTests(unittest.TestCase):
    def test_search_is_bounded_ranked_and_authorized(self) -> None:
        index = BoundedThreadIndex("root", ("child",), capacity=3)
        index.add(_entry("root", 0, "old alpha"))
        index.add(_entry("root", 1, "alpha beta"))
        index.add(_entry("child", 0, "alpha alpha"))
        index.add(_entry("root", 2, "latest beta"))

        hits = index.search("alpha beta", limit=2)

        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].entry.text, "alpha beta")
        with self.assertRaises(KeyError):
            index.get(_entry("root", 0, "old alpha").anchor)
        with self.assertRaises(ThreadAccessError):
            index.add(_entry("other", 0, "secret"))
        with self.assertRaises(ThreadAccessError):
            index.search("alpha", thread_ids=("other",))

    def test_read_thread_surfaces_later_revisions_and_actual_tool_outcomes(self) -> None:
        index = BoundedThreadIndex("root")
        primary = _entry("root", 1, "tool should succeed", call_id="call-1")
        revision = _entry(
            "root",
            2,
            "earlier claim was reverted",
            relation=EntryRelation.REVERTS,
            related_id=primary.anchor.stable_id,
        )
        outcome = _entry("root", 3, "tool failed with exit 1", call_id="call-1", is_error=True)
        for entry in (primary, revision, outcome):
            index.add(entry)

        result = ThreadReader(index).read_thread(primary.anchor)

        self.assertEqual(result.primary, primary)
        self.assertEqual(result.later_revisions, (revision,))
        self.assertEqual(result.tool_outcomes, (outcome,))
        self.assertTrue(result.conflict_candidate)


if __name__ == "__main__":
    unittest.main()
