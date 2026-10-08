import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.sessions._memory import assess_memory_applicability
from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.models import MemoryLifecycle as L
from code_agent.sessions.repository import SQLiteSessionRepository


class MemoryRetrievalTests(unittest.TestCase):
    def exercise(self, operation):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            database = (root / "explicit-test.db").resolve()
            assert database.parent == root
            with SQLiteSessionRepository(database) as repo:
                asyncio.run(operation(repo))

    def test_old_chinese_applicable_match_survives_new_noise_and_scopes(self):
        async def run(repo):
            old = await repo.create_memory("project", "alpha", "decision", "盐穴建模采用地质分层", lifecycle=L.ACTIVE, conditions={"branch": "main"})
            for index in range(120):
                await repo.create_memory("project", "alpha", "decision", f"新记录{index}无关", lifecycle=L.ACTIVE)
            for index in range(30):
                await repo.create_memory("project", "alpha", "decision", f"盐穴建模冲突{index}", lifecycle=L.ACTIVE, conditions={"branch": "other"})
                await repo.create_memory("project", "alpha", "decision", f"盐穴建模待确认{index}", lifecycle=L.ACTIVE, conditions={"unknown": "x"})
            await repo.create_memory("project", "beta", "decision", "盐穴建模秘密", lifecycle=L.ACTIVE)
            await repo.create_memory("user", "me", "decision", "盐穴建模用户秘密", lifecycle=L.ACTIVE, allow_user_scope=True)
            result = await repo.search_memories("alpha", "盐穴建模", limit=1, context={"branch": "main"}, require_applicable=True)
            self.assertEqual(result, (old,))
            diagnostic = await repo.search_memory_diagnostics("alpha", "盐穴建模", limit=1, context={"branch": "main"}, require_applicable=True)
            self.assertEqual(diagnostic["selected"], (old.memory_id + "@1",))
            self.assertEqual(diagnostic["excluded"]["applicability"], 60)
            self.assertNotIn("秘密", repr(diagnostic))
        self.exercise(run)

    def test_revision_lifecycle_rank_and_stable_pages(self):
        async def run(repo):
            first = await repo.create_memory("project", "p", "decision", "SQLite WAL", lifecycle=L.ACTIVE)
            await repo.revise_memory(first.memory_id, "use postgres", expected_revision=1, lifecycle=L.ACTIVE)
            withdrawn = await repo.create_memory("project", "p", "decision", "SQLite withdrawn", lifecycle=L.ACTIVE)
            await repo.set_memory_lifecycle(withdrawn.memory_id, revision=1, lifecycle=L.WITHDRAWN)
            ids = []
            for text in ("SQLite", "SQLite WAL chosen", "WAL"):
                item = await repo.create_memory("project", "p", "decision", text, lifecycle=L.ACTIVE)
                ids.append(item.memory_id)
            all_rows = await repo.search_memories("p", "SQLITE wal")
            self.assertEqual(all_rows[0].content, "SQLite WAL chosen")
            pages = [await repo.search_memories("p", "SQLITE wal", limit=1, offset=i) for i in range(3)]
            self.assertEqual(tuple(page[0] for page in pages), all_rows)
            self.assertEqual({item.memory_id for item in all_rows}, set(ids))
        self.exercise(run)

    def test_applicability_unconditional_and_partial_known_conditions(self):
        async def run(repo):
            unconditional = await repo.create_memory("project", "p", "decision", "SQLite default", lifecycle=L.ACTIVE)
            partial = await repo.create_memory("project", "p", "decision", "SQLite branch", lifecycle=L.ACTIVE, conditions={"branch": "main", "model": "x"})
            self.assertEqual(assess_memory_applicability(partial, {"branch": "main"}), "needs_check")
            self.assertEqual(assess_memory_applicability(partial, {"branch": "bad"}), "conflict")
            self.assertEqual(assess_memory_applicability(unconditional, {}), "needs_check")
            typed = await repo.create_memory("project", "p", "decision", "typed constraint", lifecycle=L.ACTIVE, conditions={"flag": True, "items": ["x"]})
            self.assertEqual(assess_memory_applicability(typed, {"flag": True, "items": ["x"]}), "applicable")
            self.assertEqual(assess_memory_applicability(typed, {"flag": 1, "items": ["x"]}), "conflict")
            self.assertEqual(await repo.search_memories("p", "SQLite", context={"branch": "main"}, require_applicable=True), (unconditional,))
            with self.assertRaises(ValueError):
                await repo.search_memories("p", "SQLite", require_applicable=True)
        self.exercise(run)

    def test_scoped_mutations_and_cross_scope_superseding_reject(self):
        async def run(repo):
            item = await repo.create_memory("project", "alpha", "decision", "SQLite original", lifecycle=L.ACTIVE)
            for action in (
                lambda: repo.get_memory(item.memory_id, scope_type="project", scope_id="beta"),
                lambda: repo.revise_memory(item.memory_id, "bad", expected_revision=1, scope_type="project", scope_id="beta"),
                lambda: repo.set_memory_lifecycle(item.memory_id, revision=1, lifecycle=L.WITHDRAWN, scope_type="project", scope_id="beta"),
                lambda: repo.delete_memory(item.memory_id, scope_type="project", scope_id="beta"),
                lambda: repo.create_memory("project", "beta", "decision", "replacement", supersedes=item.memory_id),
            ):
                with self.assertRaises(SessionNotFound): await action()
            self.assertEqual(await repo.get_memory(item.memory_id, scope_type="project", scope_id="alpha"), item)
            self.assertEqual(await repo.list_memories("project", "beta", include_history=True), ())
        self.exercise(run)

    def test_forget_blocks_revision_and_activation_retries(self):
        async def run(repo):
            item = await repo.create_memory("project", "p", "decision", "forgotten SQLite", lifecycle=L.ACTIVE)
            candidate = await repo.create_memory("project", "p", "other", item.content, lifecycle=L.CANDIDATE)
            other = await repo.create_memory("project", "p", "decision", "still valid", lifecycle=L.ACTIVE)
            await repo.delete_memory(item.memory_id, scope_type="project", scope_id="p")
            for action in (
                lambda: repo.create_memory("project", "p", "decision", item.content, idempotency_key="retry"),
                lambda: repo.revise_memory(other.memory_id, item.content, expected_revision=1),
                lambda: repo.set_memory_lifecycle(candidate.memory_id, revision=1, lifecycle=L.ACTIVE),
                lambda: repo.create_memory("project", "p", "decision", "different content", memory_id=item.memory_id),
            ):
                with self.assertRaisesRegex(ValueError, "forgotten"): await action()
            self.assertEqual(await repo.search_memories("p", "forgotten"), ())
            self.assertEqual(await repo.get_memory(other.memory_id, scope_type="project", scope_id="p"), other)
        self.exercise(run)

    def test_reopen_preserves_forget_and_revision_conditions_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            database = (root / "explicit-reopen.db").resolve()
            assert database.parent == root
            async def write(repo):
                record = await repo.create_memory("project", "p", "decision", "SQLite protected", source_refs={"message": 10}, conditions={"branch": "main"}, lifecycle=L.ACTIVE)
                changed = await repo.revise_memory(record.memory_id, "SQLite corrected", expected_revision=1)
                self.assertEqual(dict(changed.conditions), {"branch": "main"})
                self.assertEqual(dict(changed.source_refs), {"message": 10})
                await repo.delete_memory(record.memory_id)
                return record.memory_id
            with SQLiteSessionRepository(database) as repo:
                memory_id = asyncio.run(write(repo))
            async def read(repo):
                for content in ("SQLite protected", "SQLite corrected", "brand new"):
                    with self.assertRaisesRegex(ValueError, "forgotten"):
                        await repo.create_memory("project", "p", "decision", content, memory_id=memory_id)
                with self.assertRaisesRegex(ValueError, "forgotten"):
                    await repo.create_memory("project", "p", "decision", "SQLite corrected")
                self.assertEqual(await repo.search_memories("p", "SQLite"), ())
            with SQLiteSessionRepository(database) as repo:
                asyncio.run(read(repo))

    def test_byte_check_precedes_record_materialization_and_unicode_casefold(self):
        async def run(repo):
            huge = await repo.create_memory("project", "p", "decision", "SQLite " + "x" * (2 * 1024 * 1024), lifecycle=L.ACTIVE)
            small = await repo.create_memory("project", "p", "decision", "Straße SQLite", lifecycle=L.ACTIVE)
            from code_agent.sessions import _memory
            original = _memory._memory_from_row
            def guard(row):
                self.assertNotEqual(row["memory_id"], huge.memory_id)
                return original(row)
            with patch.object(_memory, "_memory_from_row", guard):
                self.assertEqual(await repo.search_memories("p", "STRASSE SQLite", max_bytes=1024), (small,))
                with self.assertRaisesRegex(ValueError, "max_bytes"):
                    await repo.get_memory(huge.memory_id, scope_type="project", scope_id="p", max_bytes=1024)
                with self.assertRaisesRegex(ValueError, "max_bytes"):
                    await repo.list_memories("project", "p", max_bytes=1024)
            for kwargs in ({"limit": True}, {"offset": -1}, {"max_bytes": 0}):
                with self.assertRaises(ValueError): await repo.search_memories("p", "x", **kwargs)
        self.exercise(run)
