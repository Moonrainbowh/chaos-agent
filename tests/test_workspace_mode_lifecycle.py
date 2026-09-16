from __future__ import annotations

import functools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace.git import GitWorkspace
from tests.agent_app_test_support import (
    _assert_no_worktree,
    _configured_application,
    _init_git_source,
    _tree_digest,
    workspace_mode_scope,
)


UNTRACKED_FILE_COUNT = 400
CONSTRAINED_OUTPUT_BYTES = 1024


def _add_untracked_files(root: Path, count: int = UNTRACKED_FILE_COUNT) -> None:
    for index in range(count):
        (root / f"untracked-{index:04d}.bin").write_bytes(b"x")


class LocalWorkspaceDefaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_case1_git_repository_auto_uses_the_source_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            source_before = _tree_digest(root)
            application = _configured_application(root)
            enumeration = patch.object(
                GitWorkspace,
                "changed_snapshot_paths",
                side_effect=AssertionError(
                    "auto must not enumerate a dirty workspace"
                ),
            )

            with enumeration as enumerate_dirty:
                task = await application.foreground_tasks.start(
                    "explain the change in note.py"
                )

            task_root = Path(task.contract.authorization.workspace_root)
            self.assertEqual(task_root, root)
            self.assertEqual(application.workspace_root_for(task.id), root)
            self.assertEqual(application.runtime_root_for(task.id), root)
            self.assertEqual(application.verification_root_for(task.id), root)
            self.assertEqual(_tree_digest(root), source_before)
            enumerate_dirty.assert_not_called()
            _assert_no_worktree(self, application, root)
            await application.aclose()

    async def test_case1_auto_never_enumerates_the_source_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            guards = [
                patch.object(
                    GitWorkspace,
                    name,
                    side_effect=AssertionError(f"auto must not call {name}"),
                )
                for name in ("changed_snapshot_paths", "snapshot_paths")
            ]

            with guards[0], guards[1]:
                task = await application.foreground_tasks.start("read only question")

            self.assertEqual(
                Path(task.contract.authorization.workspace_root), root
            )
            _assert_no_worktree(self, application, root)
            await application.aclose()

    async def test_case2_git_repository_direct_uses_the_source_workspace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            source_before = _tree_digest(root)
            with workspace_mode_scope("direct"):
                application = _configured_application(root)

                task = await application.foreground_tasks.start("edit note.py")

                self.assertEqual(
                    Path(task.contract.authorization.workspace_root), root
                )
                self.assertEqual(_tree_digest(root), source_before)
                _assert_no_worktree(self, application, root)
                await application.aclose()

    async def test_case4_non_git_repository_auto_uses_the_source_workspace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.py").write_text("print('plain')\n", encoding="utf-8")
            source_before = _tree_digest(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("inspect")

            self.assertEqual(
                Path(task.contract.authorization.workspace_root), root
            )
            self.assertEqual(application.workspace_root_for(task.id), root)
            self.assertEqual(_tree_digest(root), source_before)
            await application.aclose()


class ManagedWorktreeIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_case3_git_repository_managed_still_seeds_dirty_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                seed = patch.object(
                    GitWorkspace,
                    "changed_snapshot_paths",
                    autospec=True,
                    side_effect=GitWorkspace.changed_snapshot_paths,
                )

                with seed as enumerate_dirty:
                    task = await application.foreground_tasks.start(
                        "edit note.py"
                    )

                task_root = Path(task.contract.authorization.workspace_root)
                self.assertNotEqual(task_root, root)
                self.assertEqual(enumerate_dirty.call_count, 1)
                self.assertEqual(
                    (task_root / "note.py").read_text(encoding="utf-8"),
                    "print('dirty')\n",
                )
                self.assertEqual(
                    GitWorkspace(task_root).status_porcelain(),
                    GitWorkspace(root).status_porcelain(),
                )
                await application.aclose()


class DirtyWorkspaceStartupCostTests(unittest.IsolatedAsyncioTestCase):
    def _constrained_git_workspace(self) -> object:
        return functools.partial(
            GitWorkspace, max_output_bytes=CONSTRAINED_OUTPUT_BYTES
        )

    async def test_case5_many_untracked_files_do_not_delay_auto_tasks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            _add_untracked_files(root)
            source_before = _tree_digest(root)

            with patch(
                "code_agent_win.workspace_seeding.GitWorkspace",
                self._constrained_git_workspace(),
            ):
                application = _configured_application(root)
                task = await application.foreground_tasks.start("analyze")

            self.assertEqual(
                Path(task.contract.authorization.workspace_root), root
            )
            self.assertEqual(_tree_digest(root), source_before)
            _assert_no_worktree(self, application, root)
            await application.aclose()

    async def test_case6_managed_mode_keeps_the_bounded_git_protection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            _add_untracked_files(root)

            with workspace_mode_scope("managed"):
                with patch(
                    "code_agent_win.workspace_seeding.GitWorkspace",
                    self._constrained_git_workspace(),
                ):
                    application = _configured_application(root)

                    with self.assertRaisesRegex(RuntimeError, "output limit"):
                        await application.foreground_tasks.start("isolated")

                self.assertEqual(
                    await application.foreground_tasks._sessions.list_tasks(),
                    (),
                )
                _assert_no_worktree(self, application, root)
                await application.aclose()


if __name__ == "__main__":
    unittest.main()
