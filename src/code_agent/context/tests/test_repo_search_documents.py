from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.cache import FileSignature  # noqa: E402
from code_agent.context.repo_index import RepoIndexService  # noqa: E402
from code_agent.context.repo_scan import RepoFileFacts  # noqa: E402
from code_agent.context.repo_search_documents import (  # noqa: E402
    bound_search_facts,
    search_document,
)
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoSearchDocumentTests(unittest.TestCase):
    def test_per_file_byte_quota_is_history_independent(self) -> None:
        first = RepoFileFacts(
            "z.py",
            FileSignature(20, 1),
            search_text="head-" + ("中" * 20) + "-tail",
        )
        second = RepoFileFacts(
            "a.py",
            FileSignature(20, 1),
            search_text="other-" + ("文" * 20) + "-tail",
        )

        first_bounded = bound_search_facts(first, 16)
        second_bounded = bound_search_facts(second, 16)

        self.assertLessEqual(
            len(first_bounded.search_text.encode("utf-8")),
            16,
        )
        self.assertLessEqual(
            len(second_bounded.search_text.encode("utf-8")),
            16,
        )
        self.assertEqual(
            bound_search_facts(first, 16),
            first_bounded,
        )

    def test_auxiliary_terms_cover_short_ascii_and_chinese_bigrams(self) -> None:
        facts = RepoFileFacts(
            "storage.py",
            FileSignature(20, 1),
            search_text="# db 支持任务回溯",
        )

        document = search_document(facts)

        self.assertIn("db", document[3].split())
        self.assertIn("回溯", document[3].split())

    def test_contract_terms_exclude_negative_responsibilities(self) -> None:
        positive = RepoFileFacts(
            "policy/AGENTS.md",
            FileSignature(20, 1),
            search_text="- 负责：统一权限和审批策略。\n",
        )
        negative = RepoFileFacts(
            "sessions/AGENTS.md",
            FileSignature(20, 1),
            search_text="- 不负责：决定权限或执行审批。\n",
        )

        positive_document = search_document(positive)
        negative_document = search_document(negative)

        self.assertIn("权限", positive_document[4])
        self.assertNotIn("权限", negative_document[4])

    def test_close_releases_fts_and_preserves_structured_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "policy.py").write_text(
                "def authorize_action():\n    pass\n",
                encoding="utf-8",
            )
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(
                guard,
                IgnoreRules.from_workspace(root),
            )
            index = RepoIndexService(files, max_files=100)
            index.snapshot_for_turn()

            index.close()
            snapshot, lexical = index.query_for_turn("authorize_action")

        self.assertFalse(index._search_index.available)
        self.assertEqual(snapshot.entries[0].path, "policy.py")
        self.assertEqual(lexical.paths, ())


if __name__ == "__main__":
    unittest.main()
