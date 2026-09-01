from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.edit_plan_store import EditPlanStoreError
from code_agent_win.rewind_capture import RewindCaptureCoordinator
from code_agent_win.rewind_gate import WorkspaceMutationGate
from code_agent_win.task_dispatcher import TaskScopedDispatcher
from code_agent_win.workspace_mutation_pool import WorkspaceMutationPool


class _RepoIndex:
    def __init__(self) -> None:
        self.invalidated: list[tuple[str, ...]] = []

    def invalidate(self, paths: object) -> None:
        self.invalidated.append(tuple(paths))


class _Runtime:
    def __init__(self, service: object) -> None:
        self.service = service

    def services_for_root(self, root: Path) -> object:
        if root != self.service.root:
            raise AssertionError("dispatcher selected the wrong workspace")
        return self.service


def _service(root: Path) -> object:
    guard = WorkspacePathGuard(root)
    return SimpleNamespace(
        root=root,
        guard=guard,
        files=WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
        git=None,
        repo_index=_RepoIndex(),
        runtime=None,
        verification=None,
    )


class TaskScopedMutationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name).resolve()
        self.source_root = base / "source"
        self.task_root = base / "task"
        self.state_root = base / "state"
        self.source_root.mkdir()
        self.task_root.mkdir()
        (self.source_root / "note.txt").write_text("source\n", encoding="utf-8")
        (self.task_root / "note.txt").write_text("task\n", encoding="utf-8")
        self.source = _service(self.source_root)
        self.task = _service(self.task_root)
        source_editor = WorkspaceEditor(self.source.guard)
        snapshots = WorkspaceSnapshotStore(
            self.source.guard, self.state_root / "rewind-snapshots"
        )
        gate = WorkspaceMutationGate(
            self.state_root, snapshots.workspace_fingerprint
        )
        sessions = RewindSessionRepository(self.state_root / "sessions.sqlite3")
        self.source_capture = RewindCaptureCoordinator(
            sessions, source_editor, snapshots, gate
        )
        self.pool = WorkspaceMutationPool(self.source, self.source_capture)
        self.dispatcher = TaskScopedDispatcher(
            _Runtime(self.task),
            self.source,
            ActionPolicy(
                PolicyConfig(
                    ApprovalMode.UNRESTRICTED,
                    workspace_root=self.source_root,
                )
            ),
            ApprovalBroker(),
            capture=self.source_capture,
            mutations=self.pool,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _context(self, request_id: str) -> ActionExecutionContext:
        return ActionExecutionContext(
            "owner", "origin", request_id, "task-1", "parent"
        )

    async def _dispatch(self, request: ActionRequest):
        return await self.dispatcher.dispatch(
            request,
            CancellationToken(),
            TaskAuthorization.local_workspace(str(self.task_root)),
            execution_context=self._context(request.id),
        )

    async def test_legacy_write_uses_task_capture_and_preserves_source(self) -> None:
        capture_roots: list[Path] = []

        async def apply_edit(capture, context, request, plan) -> None:
            del context, request
            capture_roots.append(capture.editor.guard.root)
            await asyncio.to_thread(capture.editor.apply, plan)

        with patch.object(RewindCaptureCoordinator, "apply_edit", new=apply_edit):
            result = await self._dispatch(
                ActionRequest(
                    "write-task",
                    "write_file",
                    {"path": "note.txt", "content": "changed\n"},
                )
            )

        self.assertFalse(result.is_error)
        self.assertEqual(capture_roots, [self.task_root])
        self.assertEqual(
            (self.task_root / "note.txt").read_text(encoding="utf-8"),
            "changed\n",
        )
        self.assertEqual(
            (self.source_root / "note.txt").read_text(encoding="utf-8"),
            "source\n",
        )

    async def test_plan_survives_second_dispatch_and_batch_uses_task_capture(self) -> None:
        capture_roots: list[Path] = []

        async def apply_edit_plan(
            capture, context, request, plan, plan_id, cancellation
        ):
            del context, request, plan_id, cancellation
            capture_roots.append(capture.editor.guard.root)
            return await asyncio.to_thread(capture.editor.apply_batch, plan)

        planned = await self._dispatch(
            ActionRequest(
                "plan-task",
                "plan_workspace_edits_v1",
                {
                    "operations": [
                        {"kind": "write", "path": "created.txt", "content": "task"}
                    ]
                },
            )
        )
        self.assertFalse(planned.is_error)
        task_bundle = self.pool.for_services(self.task)
        source_bundle = self.pool.for_services(self.source)
        self.assertIs(task_bundle, self.pool.for_services(self.task))
        self.assertIs(task_bundle.editor, task_bundle.capture.editor)
        self.assertIs(task_bundle.gate, task_bundle.capture.gate)
        self.assertIs(task_bundle.snapshots, task_bundle.capture.snapshots)
        self.assertIsNot(task_bundle.gate, source_bundle.gate)
        self.assertIsNot(task_bundle.snapshots, source_bundle.snapshots)
        self.assertIsNotNone(task_bundle.coordinated)
        self.assertIsNot(task_bundle.coordinated, source_bundle.coordinated)
        self.assertNotEqual(
            task_bundle.workspace_fingerprint,
            source_bundle.workspace_fingerprint,
        )
        self.assertIsNot(task_bundle.edit_plans, source_bundle.edit_plans)
        with self.assertRaises(EditPlanStoreError):
            source_bundle.edit_plans.get(planned.output["plan_id"])

        with patch.object(
            RewindCaptureCoordinator,
            "apply_edit_plan",
            create=True,
            new=apply_edit_plan,
        ):
            applied = await self._dispatch(
                ActionRequest(
                    "apply-task",
                    "apply_workspace_edit_plan_v1",
                    {
                        "plan_id": planned.output["plan_id"],
                        "plan_digest": planned.output["plan_digest"],
                    },
                )
            )

        self.assertFalse(applied.is_error)
        self.assertEqual(capture_roots, [self.task_root])
        self.assertEqual((self.task_root / "created.txt").read_text(), "task")
        self.assertFalse((self.source_root / "created.txt").exists())


if __name__ == "__main__":
    unittest.main()
