from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces._diff_parser import DiffScope
from code_agent_win.app_ui import GitDiffAdapter, TaskScopedGitDiffAdapter


class _Git:
    def __init__(self, marker: str = "") -> None:
        self.marker = marker
        self.paths: tuple[str, ...] = ()

    def diff_snapshot(self, paths: tuple[str, ...]) -> object:
        self.paths = paths
        return SimpleNamespace(
            staged=f"--- a/{self.marker}staged.py\n+++ b/{self.marker}staged.py\n",
            unstaged=f"--- a/{self.marker}work.py\n+++ b/{self.marker}work.py\n",
            untracked=f"--- /dev/null\n+++ b/{self.marker}new.py\n",
        )


class DiffRuntimeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_adapter_preserves_all_git_snapshot_facets(self) -> None:
        git = _Git()

        documents = await GitDiffAdapter(git).read_diff(("src",))

        self.assertEqual(
            tuple(document.scope for document in documents),
            (DiffScope.STAGED, DiffScope.UNSTAGED, DiffScope.UNTRACKED),
        )
        self.assertTrue(all(document.fresh for document in documents))
        self.assertEqual(git.paths, ("src",))

    async def test_task_adapter_reads_the_bound_workspace_snapshot(self) -> None:
        root_git = _Git("root-")
        task_git = _Git("task-")

        class Runtime:
            async def hydrate_bindings(self) -> None:
                self.hydrated = True

            def root_for_task(self, task_id: str) -> str:
                self.task_id = task_id
                return "task-root"

            def services_for_root(self, root: str) -> object:
                self.root = root
                return SimpleNamespace(git=task_git)

        runtime = Runtime()
        adapter = TaskScopedGitDiffAdapter(
            runtime,
            lambda: "task-1",
            GitDiffAdapter(root_git),
        )

        documents = await adapter.read_diff()

        self.assertTrue(runtime.hydrated)
        self.assertEqual(runtime.task_id, "task-1")
        self.assertIn("task-staged.py", documents[0].unified)
        self.assertEqual(root_git.paths, ())


if __name__ == "__main__":
    unittest.main()
