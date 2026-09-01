from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import (
    BatchApplyResult,
    BatchApplyStatus,
    BatchConflict,
    WorkspaceEditor,
)
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.edit_plan_store import WorkspaceEditPlanStore
from code_agent_win.edit_plan_store import StoredPlanStatus


_FINGERPRINT = "a" * 64


class EditPlanDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "delete.txt").write_text("owned\n", encoding="utf-8")
        guard = WorkspacePathGuard(self.root)
        self.approvals = ApprovalBroker()
        identifiers = iter(("1" * 32, "2" * 32, "3" * 32))
        self.store = WorkspaceEditPlanStore(id_factory=lambda: next(identifiers))
        self.invalidated: list[tuple[str, ...]] = []
        self.dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(
                PolicyConfig(ApprovalMode.UNRESTRICTED, workspace_root=self.root)
            ),
            self.approvals,
            edit_plans=self.store,
            workspace_fingerprint=_FINGERPRINT,
            invalidate_cache=lambda paths: self.invalidated.append(tuple(paths)),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _context(request_id: str) -> ActionExecutionContext:
        return ActionExecutionContext(
            "owner", "origin", request_id, "task-1", "parent"
        )

    async def _dispatch(self, request: ActionRequest):
        return await self.dispatcher.dispatch(
            request,
            CancellationToken(),
            execution_context=self._context(request.id),
        )

    async def test_plan_is_zero_write_and_apply_uses_only_id_and_digest(self) -> None:
        planned = await self._dispatch(
            ActionRequest(
                "plan-1",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "write", "path": "new.txt", "content": ""}]},
            )
        )
        self.assertFalse(planned.is_error)
        self.assertEqual(planned.output["status"], "planned")
        self.assertFalse((self.root / "new.txt").exists())

        applied = await self._dispatch(
            ActionRequest(
                "apply-1",
                "apply_workspace_edit_plan_v1",
                {
                    "plan_id": planned.output["plan_id"],
                    "plan_digest": planned.output["plan_digest"],
                },
            )
        )

        self.assertFalse(applied.is_error)
        self.assertEqual(applied.output["status"], "applied")
        self.assertIs(applied.output["workspace_may_have_changed"], True)
        self.assertEqual(applied.output["paths"], ("new.txt",))
        self.assertEqual((self.root / "new.txt").read_bytes(), b"")
        self.assertEqual(self.invalidated, [("new.txt",)])

    async def test_non_git_delete_forces_preview_approval_even_unrestricted(self) -> None:
        planned = await self._dispatch(
            ActionRequest(
                "plan-delete",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "delete", "path": "delete.txt"}]},
            )
        )
        denied = await self._dispatch(
            ActionRequest(
                "apply-delete",
                "apply_workspace_edit_plan_v1",
                {
                    "plan_id": planned.output["plan_id"],
                    "plan_digest": planned.output["plan_digest"],
                },
            )
        )

        self.assertTrue(denied.is_error)
        self.assertEqual(denied.output["error_code"], "approval_required")
        self.assertTrue((self.root / "delete.txt").exists())

    async def test_interactive_approval_contains_trusted_batch_diff(self) -> None:
        planned = await self._dispatch(
            ActionRequest(
                "plan-delete",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "delete", "path": "delete.txt"}]},
            )
        )
        self.dispatcher.interactive = True
        apply_request = ActionRequest(
            "apply-delete",
            "apply_workspace_edit_plan_v1",
            {
                "plan_id": planned.output["plan_id"],
                "plan_digest": planned.output["plan_digest"],
            },
        )
        running = asyncio.create_task(self._dispatch(apply_request))
        approval = await asyncio.wait_for(self.approvals.next_request(), 2)
        self.assertIsNotNone(approval.edit_plan)
        assert approval.edit_plan is not None
        self.assertEqual(approval.edit_plan.plan_id, planned.output["plan_id"])
        self.assertIn("delete.txt", approval.edit_plan.combined_diff)
        self.approvals.resolve(approval.request_id, True)
        applied = await running

        self.assertFalse(applied.is_error)
        self.assertFalse((self.root / "delete.txt").exists())

    async def test_drift_after_planning_preserves_user_file(self) -> None:
        planned = await self._dispatch(
            ActionRequest(
                "plan-create",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "write", "path": "raced.txt", "content": "agent"}]},
            )
        )
        (self.root / "raced.txt").write_text("user", encoding="utf-8")
        applied = await self._dispatch(
            ActionRequest(
                "apply-create",
                "apply_workspace_edit_plan_v1",
                {
                    "plan_id": planned.output["plan_id"],
                    "plan_digest": planned.output["plan_digest"],
                },
            )
        )
        self.assertTrue(applied.is_error)
        self.assertEqual(applied.output["error_code"], "edit_plan_conflict")
        self.assertEqual((self.root / "raced.txt").read_text(encoding="utf-8"), "user")

    async def test_pre_cancelled_apply_keeps_plan_retryable_and_writes_nothing(self) -> None:
        planned = await self._dispatch(
            ActionRequest(
                "plan-cancelled",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "write", "path": "cancelled.txt", "content": "agent"}]},
            )
        )
        token = CancellationToken()
        token.cancel("user interrupted")
        apply_request = ActionRequest(
            "apply-cancelled",
            "apply_workspace_edit_plan_v1",
            {
                "plan_id": planned.output["plan_id"],
                "plan_digest": planned.output["plan_digest"],
            },
        )

        with self.assertRaises(CancellationError):
            await self.dispatcher.dispatch(
                apply_request,
                token,
                execution_context=self._context(apply_request.id),
            )

        self.assertFalse((self.root / "cancelled.txt").exists())
        self.assertEqual(
            self.store.get(planned.output["plan_id"]).status,
            StoredPlanStatus.PLANNED,
        )

    async def test_partial_conflict_reports_each_preserved_path_and_reason(self) -> None:
        class ConflictCapture:
            workspace_fingerprint = _FINGERPRINT

            async def apply_edit_plan(self, *args):
                return BatchApplyResult(
                    BatchApplyStatus.PARTIAL_CONFLICT,
                    conflicts=(BatchConflict("raced.txt", "owned postimage drifted"),),
                    error="injected",
                )

        self.dispatcher.capture = ConflictCapture()
        self.dispatcher.edit_plan_actions.capture = self.dispatcher.capture
        planned = await self._dispatch(
            ActionRequest(
                "plan-partial",
                "plan_workspace_edits_v1",
                {"operations": [{"kind": "write", "path": "raced.txt", "content": "agent"}]},
            )
        )
        result = await self._dispatch(
            ActionRequest(
                "apply-partial",
                "apply_workspace_edit_plan_v1",
                {
                    "plan_id": planned.output["plan_id"],
                    "plan_digest": planned.output["plan_digest"],
                },
            )
        )

        self.assertTrue(result.is_error)
        self.assertIs(result.output["workspace_may_have_changed"], True)
        self.assertEqual(result.output["paths"], ("raced.txt",))
        self.assertEqual(
            result.output["conflicts"],
            ({"path": "raced.txt", "reason": "owned postimage drifted"},),
        )

    async def test_unproved_recovery_reports_possible_workspace_change(self) -> None:
        class FailedCapture:
            workspace_fingerprint = _FINGERPRINT

            async def apply_edit_plan(self, *args):
                raise RuntimeError("lost worker result")

        self.dispatcher.capture = FailedCapture()
        self.dispatcher.edit_plan_actions.capture = self.dispatcher.capture
        planned = await self._dispatch(ActionRequest(
            "plan-failed", "plan_workspace_edits_v1",
            {"operations": [{"kind": "write", "path": "maybe.txt", "content": "agent"}]},
        ))
        result = await self._dispatch(ActionRequest(
            "apply-failed", "apply_workspace_edit_plan_v1",
            {"plan_id": planned.output["plan_id"], "plan_digest": planned.output["plan_digest"]},
        ))

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "recovery_required")
        self.assertIs(result.output["workspace_may_have_changed"], True)
        self.assertEqual(result.output["paths"], ("maybe.txt",))

    async def test_complete_rollback_reports_no_workspace_change(self) -> None:
        class RolledBackCapture:
            workspace_fingerprint = _FINGERPRINT

            async def apply_edit_plan(self, *args):
                return BatchApplyResult(BatchApplyStatus.ROLLED_BACK, error="injected")

        self.dispatcher.capture = RolledBackCapture()
        self.dispatcher.edit_plan_actions.capture = self.dispatcher.capture
        planned = await self._dispatch(ActionRequest(
            "plan-rolled", "plan_workspace_edits_v1",
            {"operations": [{"kind": "write", "path": "clean.txt", "content": "agent"}]},
        ))
        result = await self._dispatch(ActionRequest(
            "apply-rolled", "apply_workspace_edit_plan_v1",
            {"plan_id": planned.output["plan_id"], "plan_digest": planned.output["plan_digest"]},
        ))

        self.assertTrue(result.is_error)
        self.assertIs(result.output["workspace_may_have_changed"], False)
        self.assertEqual(result.output["paths"], ())


if __name__ == "__main__":
    unittest.main()
