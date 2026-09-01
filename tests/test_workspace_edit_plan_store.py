from __future__ import annotations

import unittest

from code_agent_win.edit_plan_store import (
    EditPlanStoreError,
    StoredPlanStatus,
    WorkspaceEditPlanStore,
)


_DIGEST = "a" * 64
_FINGERPRINT = "b" * 64


class WorkspaceEditPlanStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        identifiers = iter(("1" * 32, "2" * 32))
        self.store = WorkspaceEditPlanStore(id_factory=lambda: next(identifiers))

    def test_plan_is_bound_to_digest_workspace_and_owner(self) -> None:
        plan = object()
        stored = self.store.save(
            plan,
            plan_digest=_DIGEST,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id="task-1",
            risk_flags=("move",),
            dirty_paths=("src/a.py",),
        )

        loaded = self.store.require_applicable(
            stored.plan_id,
            _DIGEST,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id="task-1",
        )

        self.assertIs(loaded.plan, plan)
        self.assertEqual(loaded.status, StoredPlanStatus.PLANNED)
        self.assertEqual(loaded.risk_flags, ("move",))
        self.assertEqual(loaded.dirty_paths, ("src/a.py",))

    def test_mismatched_identity_never_returns_the_plan(self) -> None:
        stored = self.store.save(
            object(),
            plan_digest=_DIGEST,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
        )
        mismatches = (
            ("c" * 64, _FINGERPRINT, "thread-1", None, "edit_plan_digest_mismatch"),
            (_DIGEST, "d" * 64, "thread-1", None, "edit_plan_workspace_mismatch"),
            (_DIGEST, _FINGERPRINT, "thread-2", None, "edit_plan_owner_mismatch"),
            (_DIGEST, _FINGERPRINT, "thread-1", "task-2", "edit_plan_owner_mismatch"),
        )
        for digest, workspace, owner, task, code in mismatches:
            with self.subTest(code=code), self.assertRaises(EditPlanStoreError) as raised:
                self.store.require_applicable(
                    stored.plan_id,
                    digest,
                    workspace_fingerprint=workspace,
                    owner_thread_id=owner,
                    task_id=task,
                )
            self.assertEqual(raised.exception.code, code)

    def test_superseding_creates_a_new_plan_and_invalidates_old_plan(self) -> None:
        first = self.store.save(
            object(),
            plan_digest=_DIGEST,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
        )
        second = self.store.save(
            object(),
            plan_digest="e" * 64,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
            supersedes_plan_id=first.plan_id,
        )

        self.assertNotEqual(first.plan_id, second.plan_id)
        self.assertEqual(
            self.store.get(first.plan_id).status,
            StoredPlanStatus.SUPERSEDED,
        )
        with self.assertRaises(EditPlanStoreError) as raised:
            self.store.require_applicable(
                first.plan_id,
                _DIGEST,
                workspace_fingerprint=_FINGERPRINT,
                owner_thread_id="thread-1",
                task_id=None,
            )
        self.assertEqual(raised.exception.code, "edit_plan_not_applicable")

    def test_apply_transition_is_one_way_and_idempotent_at_terminal_state(self) -> None:
        stored = self.store.save(
            object(),
            plan_digest=_DIGEST,
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
        )
        applying = self.store.begin_apply(stored.plan_id)
        self.assertEqual(applying.status, StoredPlanStatus.APPLYING)
        applied = self.store.settle(stored.plan_id, StoredPlanStatus.APPLIED)
        self.assertEqual(applied.status, StoredPlanStatus.APPLIED)
        self.assertEqual(
            self.store.settle(stored.plan_id, StoredPlanStatus.APPLIED), applied
        )
        with self.assertRaises(EditPlanStoreError) as raised:
            self.store.settle(stored.plan_id, StoredPlanStatus.CONFLICTED)
        self.assertEqual(raised.exception.code, "edit_plan_not_applicable")


if __name__ == "__main__":
    unittest.main()
