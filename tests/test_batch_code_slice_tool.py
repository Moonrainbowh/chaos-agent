from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.context.repo_index import RepoIndexService
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher


class BatchCodeSliceToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "app.py").write_bytes(b"one\ntwo\nthree\n")
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.index = RepoIndexService(self.files)
        self.dispatcher = RootActionDispatcher(
            self.files,
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)),
            ApprovalBroker(),
            repo_index=self.index,
        )

    def tearDown(self) -> None:
        self.index.close()
        self.temporary.cleanup()

    def arguments(self) -> dict[str, object]:
        snapshot = self.index.snapshot_for_turn()
        entry = next(item for item in snapshot.entries if item.path == "app.py")
        return {
            "generation": snapshot.generation,
            "targets": [{
                "path": "app.py",
                "start_line": 2,
                "end_line": 3,
                "expected_size_bytes": entry.signature.size_bytes,
                "expected_modified_ns": entry.signature.modified_ns,
                "expected_device_id": entry.signature.device_id,
                "expected_file_id": entry.signature.file_id,
            }],
        }

    async def test_dispatches_a_generation_bound_batch(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest("slice-1", "read_code_slices", self.arguments()),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["slices"][0]["text"], "two\nthree\n")
        self.assertEqual(result.output["slices"][0]["path"], "app.py")

    async def test_generation_mismatch_fails_without_source(self) -> None:
        arguments = self.arguments()
        arguments["generation"] = int(arguments["generation"]) + 1

        result = await self.dispatcher.dispatch(
            ActionRequest("slice-stale", "read_code_slices", arguments),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "stale_repo_context")
        self.assertNotIn("slices", result.output)

    async def test_generation_change_during_read_fails_without_partial_source(self) -> None:
        arguments = self.arguments()
        real_read = self.files.read_code_slices

        def read_then_publish(targets):
            slices = real_read(targets)
            (self.root / "new.py").write_bytes(b"value = 1\n")
            self.index.invalidate(("new.py",))
            self.index.snapshot_for_turn()
            return slices

        with patch.object(self.files, "read_code_slices", side_effect=read_then_publish):
            result = await self.dispatcher.dispatch(
                ActionRequest("slice-race", "read_code_slices", arguments),
                CancellationToken(),
            )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "stale_repo_context")
        self.assertNotIn("slices", result.output)

    async def test_pending_invalidation_during_read_is_refreshed_before_return(self) -> None:
        arguments = self.arguments()
        real_read = self.files.read_code_slices

        def read_then_invalidate(targets):
            slices = real_read(targets)
            (self.root / "other.py").write_bytes(b"value = 2\n")
            self.index.invalidate(("other.py",))
            return slices

        with patch.object(self.files, "read_code_slices", side_effect=read_then_invalidate):
            result = await self.dispatcher.dispatch(
                ActionRequest("slice-pending", "read_code_slices", arguments),
                CancellationToken(),
            )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "stale_repo_context")
        self.assertNotIn("slices", result.output)


if __name__ == "__main__":
    unittest.main()
