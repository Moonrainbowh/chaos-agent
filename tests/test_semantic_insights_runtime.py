from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.context.models import RepoEntry
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.context.repo_search import RepoLexicalRanks
from code_agent_win.semantic_insights import SemanticGraphControl


class _Index:
    def __init__(self, snapshot: RepoIndexSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def snapshot_for_turn(self) -> RepoIndexSnapshot:
        self.calls += 1
        return self.snapshot

    def invalidate(self, paths) -> None:
        self.invalidated = paths

    def query_for_turn(self, query: str):
        self.query = query
        self.calls += 1
        return self.snapshot, RepoLexicalRanks(term_paths=("src/app.py",))


class _WorkspaceRuntime:
    def __init__(self, source: Path, task: Path, index: _Index) -> None:
        self.source, self.task, self.index = source, task, index
        self.hydrated = False

    def root_for_thread(self, thread_id: str) -> Path | None:
        return self.task if self.hydrated and thread_id == "task-thread" else None

    async def hydrate_bindings(self) -> None:
        self.hydrated = True

    def services_for_root(self, root: Path) -> object:
        self.seen_root = Path(root).resolve()
        return SimpleNamespace(repo_index=self.index)


class SemanticGraphControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_active_thread_root_and_shared_index_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            task = Path(directory) / "task"
            source.mkdir()
            task.mkdir()
            snapshot = RepoIndexSnapshot(4, (RepoEntry("src/app.py"),))
            index = _Index(snapshot)
            runtime = _WorkspaceRuntime(source, task, index)
            control = SemanticGraphControl(source, runtime)

            report = await control.analyze(
                "overview", thread_id="task-thread"
            )

        self.assertEqual(report.generation, 4)
        self.assertEqual(runtime.seen_root, task.resolve())
        self.assertEqual(index.calls, 1)
        self.assertEqual(index.invalidated, ())
        self.assertIn(str(task.resolve()), report.warnings[0])

    async def test_bug_location_uses_same_index_lexical_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            snapshot = RepoIndexSnapshot(5, (RepoEntry("src/app.py"),))
            index = _Index(snapshot)
            runtime = _WorkspaceRuntime(source, source, index)
            control = SemanticGraphControl(source, runtime)

            report = await control.analyze("locate", ("opaque failure",))

        self.assertEqual(index.query, "opaque failure")
        self.assertEqual(report.sections[0].items[0].label, "src/app.py")


if __name__ == "__main__":
    unittest.main()
