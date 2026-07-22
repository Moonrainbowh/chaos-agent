from __future__ import annotations

import unittest

from code_agent.sessions.models import ThreadRelation
from code_agent.thread_intelligence.authorization import ThreadAuthorization
from code_agent.thread_intelligence.index import ThreadAccessError


class RelationStore:
    def __init__(self) -> None:
        self.relations = {
            "root": ThreadRelation("root", None, ("child-a", "child-b")),
            "child-a": ThreadRelation("child-a", "root"),
            "child-b": ThreadRelation("child-b", "root"),
            "other": ThreadRelation("other", None),
        }

    async def load_thread_relation(self, thread_id: str) -> ThreadRelation:
        return self.relations[thread_id]


class ThreadAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_can_read_self_and_direct_children(self) -> None:
        authorization = ThreadAuthorization(RelationStore())

        self.assertEqual(
            await authorization.authorized_threads("root"),
            frozenset({"root", "child-a", "child-b"}),
        )

    async def test_child_can_read_only_self_and_parent(self) -> None:
        authorization = ThreadAuthorization(RelationStore())

        self.assertEqual(
            await authorization.authorized_threads("child-a"),
            frozenset({"root", "child-a"}),
        )
        with self.assertRaises(ThreadAccessError):
            await authorization.ensure_can_read("child-a", "child-b")

    async def test_unrelated_thread_is_denied(self) -> None:
        authorization = ThreadAuthorization(RelationStore())

        with self.assertRaises(ThreadAccessError):
            await authorization.ensure_can_read("root", "other")


if __name__ == "__main__":
    unittest.main()
