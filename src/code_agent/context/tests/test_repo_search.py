from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_index import RepoIndexService  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.repo_search import SQLiteRepoSearch  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.files = self._files(self.root)
        self.config = ContextConfig(
            self.root,
            self.root,
            "System",
            repo_scan=100,
            repo_map_tokens=200,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _files(root: Path) -> WorkspaceFiles:
        guard = WorkspacePathGuard(root)
        return WorkspaceFiles(guard, IgnoreRules.from_workspace(root))

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_chinese_body_and_short_phrase_drive_file_ranking(self) -> None:
        self.write(
            "policy/approval.py",
            "# 危险命令执行前统一进行权限校验\n"
            "def authorize_action(command):\n"
            "    return command\n",
        )
        self.write(
            "sessions/restore.py",
            "# 恢复历史会话和检查点\n"
            "def restore_thread():\n"
            "    pass\n",
        )
        self.write(
            "workspace/errors.py",
            "class PermissionError(Exception):\n"
            "    pass\n",
        )
        builder = RepoMapBuilder(self.files, self.config)

        self.assertEqual(
            builder.build(query="权限校验在哪里实现")[0].path,
            "policy/approval.py",
        )
        self.assertEqual(
            builder.build(query="危险命令")[0].path,
            "policy/approval.py",
        )
        self.assertEqual(
            builder.build(query="权限")[0].path,
            "policy/approval.py",
        )
        self.assertEqual(
            builder.build(query="恢复历史会话")[0].path,
            "sessions/restore.py",
        )

    def test_chinese_sentence_can_recall_a_feature_contract(self) -> None:
        self.write(
            "policy/AGENTS.md",
            "# Action Policy\n负责统一权限和审批策略。\n",
        )
        self.write(
            "policy/engine.py",
            "class ActionPolicy:\n"
            "    def evaluate(self, request):\n"
            "        return request\n",
        )
        self.write(
            "sessions/AGENTS.md",
            "# Sessions\n- 不负责：决定权限或执行审批。\n",
        )
        self.write(
            "sessions/codec.py",
            "def decode_session(value):\n"
            "    return value\n",
        )
        self.write("workspace/errors.py", "class PermissionError(Exception):\n    pass\n")
        builder = RepoMapBuilder(self.files, self.config)

        ranked = builder.build(query="权限校验在哪里实现")

        self.assertEqual(ranked[0].path, "policy/engine.py")

    def test_chinese_intent_recalls_english_only_implementation(self) -> None:
        self.write("aaa.py", "def render_banner():\n    return 'ready'\n")
        self.write(
            "policy/engine.py",
            "class ActionPolicy:\n"
            "    def authorize_action(self, request):\n"
            "        return self.validate_permission(request)\n"
            "\n"
            "    def validate_permission(self, request):\n"
            "        return request\n",
        )
        builder = RepoMapBuilder(self.files, self.config)

        ranked = builder.build(query="权限校验在哪里实现")

        self.assertEqual(ranked[0].path, "policy/engine.py")

    def test_long_chinese_query_keeps_tail_intent_terms(self) -> None:
        self.write("aaa.py", "# 普通项目说明\n")
        self.write(
            "policy.py",
            "# 危险命令执行前进行权限校验\n"
            "def authorize_action():\n"
            "    pass\n",
        )
        builder = RepoMapBuilder(self.files, self.config)

        ranked = builder.build(
            query=(
                "你好请帮我仔细分析一下这个项目中究竟是在哪里负责"
                "危险命令执行前权限校验的代码"
            )
        )

        self.assertEqual(ranked[0].path, "policy.py")

    def test_distinct_cjk_query_tail_is_recalled(self) -> None:
        query = "".join(chr(0x4E00 + offset) for offset in range(220))
        self.write("aaa.py", "# unrelated\n")
        self.write("tail.py", f"# {query[-3:]}\n")

        ranked = RepoMapBuilder(self.files, self.config).build(query=query)

        self.assertEqual(ranked[0].path, "tail.py")

    def test_incremental_search_replaces_and_removes_body(self) -> None:
        self.write("policy.py", "# 废弃暗号\nclass Policy:\n    pass\n")
        index = RepoIndexService(self.files, max_files=100)

        first, first_ranks = index.query_for_turn("废弃暗号")
        self.assertEqual(first.generation, 1)
        self.assertEqual(first_ranks.paths[0], "policy.py")
        self.write(
            "policy.py",
            "# 全新口令\nclass PolicyChanged:\n    pass\n",
        )
        index.invalidate(("policy.py",))

        updated, new_ranks = index.query_for_turn("全新口令")
        _, old_ranks = index.query_for_turn("废弃暗号")

        self.assertEqual(updated.generation, 2)
        self.assertEqual(new_ranks.paths[0], "policy.py")
        self.assertNotIn("policy.py", old_ranks.paths)
        (self.root / "policy.py").unlink()
        index.invalidate(("policy.py",))

        removed, removed_ranks = index.query_for_turn("全新口令")

        self.assertEqual(removed.generation, 3)
        self.assertEqual(removed.entries, ())
        self.assertNotIn("policy.py", removed_ranks.paths)

    def test_query_operators_and_quotes_are_treated_as_data(self) -> None:
        self.write(
            "policy.py",
            "# 权限校验\n"
            "def authorize_action():\n"
            "    pass\n",
        )
        index = RepoIndexService(self.files, max_files=100)

        for query in ('"', "*", "OR", "NEAR", "()", '权限" OR *'):
            snapshot, ranks = index.query_for_turn(query)
            self.assertEqual(snapshot.entries[0].path, "policy.py")
            self.assertIsInstance(ranks.paths, tuple)

    def test_unavailable_fts_falls_back_to_structured_ranking(self) -> None:
        self.write(
            "policy.py",
            "def authorize_action():\n"
            "    pass\n",
        )

        def unavailable() -> sqlite3.Connection:
            raise sqlite3.OperationalError("no such module: fts5")

        search = SQLiteRepoSearch(connection_factory=unavailable)
        index = RepoIndexService(
            self.files,
            max_files=100,
            search_index=search,
        )
        builder = RepoMapBuilder(
            self.files,
            self.config,
            index=index,
        )

        self.assertFalse(search.available)
        self.assertEqual(
            builder.build(query="authorize_action")[0].path,
            "policy.py",
        )

    def test_terms_only_fts_uses_chinese_substring_fallback(self) -> None:
        self.write("aaa.py", "# 无关内容\n")
        self.write(
            "policy.py",
            "# 危险命令执行前进行权限校验\n"
            "def authorize_action():\n"
            "    pass\n",
        )

        class TermsOnlyConnection(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):  # type: ignore[no-untyped-def]
                if "tokenize='trigram'" in sql:
                    raise sqlite3.OperationalError(
                        "no such tokenizer: trigram"
                    )
                return super().execute(sql, parameters)

        def terms_only() -> sqlite3.Connection:
            return sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                factory=TermsOnlyConnection,
            )

        search = SQLiteRepoSearch(connection_factory=terms_only)
        index = RepoIndexService(
            self.files,
            max_files=100,
            search_index=search,
        )
        builder = RepoMapBuilder(self.files, self.config, index=index)

        self.assertTrue(search.available)
        _, ranks = index.query_for_turn("权限校验在哪里实现")
        self.assertEqual(ranks.paths[0], "policy.py")
        self.assertEqual(
            builder.build(query="权限校验在哪里实现")[0].path,
            "policy.py",
        )

    def test_short_ascii_body_term_is_recalled(self) -> None:
        self.write("aaa.py", "# unrelated content\n")
        self.write("storage.py", "# db migration logic\n")
        index = RepoIndexService(self.files, max_files=100)

        _, ranks = index.query_for_turn("db")

        self.assertEqual(ranks.paths[0], "storage.py")

    def test_concurrent_warm_queries_are_consistent(self) -> None:
        self.write("alpha.py", "# 并发检索\nclass Alpha:\n    pass\n")
        index = RepoIndexService(self.files, max_files=100)
        index.snapshot_for_turn()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = tuple(
                executor.map(
                    lambda _: index.query_for_turn("并发检索")[1].paths,
                    range(16),
                )
            )

        self.assertTrue(all(item == ("alpha.py",) for item in results))

if __name__ == "__main__":
    unittest.main()
