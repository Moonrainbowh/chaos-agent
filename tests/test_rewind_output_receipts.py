"""Actual output provenance across the apply-to-journal cancellation window."""
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import EditBatchState
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import BatchApplyStatus, WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from chaos_agent.rewind_capture import RewindCaptureCoordinator


class _Lease:
    async def release(self):
        pass


class _Gate:
    async def acquire(self):
        return _Lease()


@unittest.skipUnless(os.name == "nt", "native exact move matrix")
class OutputReceiptIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="s14-host-receipts-")
        base = Path(self.temporary.name).resolve()
        self.root = base / "workspace"
        database, artifacts = base / "sessions.sqlite3", base / "snapshots"
        assert all(path.is_absolute() and path.is_relative_to(base)
                   for path in (self.root, database, artifacts))
        self.root.mkdir()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))
        self.sessions = RewindSessionRepository(database)
        self.snapshots = WorkspaceSnapshotStore(self.editor.guard, artifacts)
        self.capture = RewindCaptureCoordinator(
            self.sessions, self.editor, self.snapshots, _Gate())
        self.owner = await self.sessions.create_thread()

    async def asyncTearDown(self):
        self.sessions.close()
        self.temporary.cleanup()

    def _plan(self, kind):
        source = self.root / "source.txt"
        if kind != "create":
            source.write_bytes(b"before")
        if kind in ("create", "update"):
            operation = self.editor.plan_write(source.name, "after")
            target, content = source, b"after"
        elif kind == "delete":
            operation = self.editor.plan_delete(source.name)
            target, content = source, b"foreign after delete"
        else:
            target = self.root / ("SOURCE.txt" if kind == "case" else "moved.txt")
            operation = self.editor.plan_move(source.name, target.name)
            content = b"before"
        return self.editor.plan_batch((operation,)), target, content

    async def _apply_cancel(self, kind, foreign):
        plan, target, content = self._plan(kind)
        real_apply, token = self.editor.apply_batch, CancellationToken()

        def interrupted(batch):
            result = real_apply(batch)
            if foreign:
                replacement = self.root / "foreign.txt"
                replacement.write_bytes(content)
                os.replace(replacement, target)
            token.cancel("after actual batch output")
            return result

        request = ActionRequest(kind, "apply_workspace_edit_plan_v1", {})
        context = ActionExecutionContext(self.owner, self.owner, kind)
        with (patch.object(self.editor, "apply_batch", side_effect=interrupted),
              patch.object(self.editor, "post_identities", side_effect=AssertionError(
                  "pathname observation must never authorize ownership"))):
            result = await self.capture.apply_edit_plan(
                context, request, plan, "stored-" + kind, token)
        records = await self.sessions.list_edit_batches(self.snapshots.workspace_fingerprint)
        return result, records[-1], target, content

    async def test_foreign_same_bytes_and_recreated_delete_are_preserved(self):
        for kind in ("create", "update", "move", "case", "delete"):
            with self.subTest(kind=kind):
                result, record, target, content = await self._apply_cancel(kind, True)
                self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
                self.assertEqual(record.state, EditBatchState.CONFLICTED)
                self.assertEqual(target.read_bytes(), content)
                for path in self.root.iterdir():
                    path.unlink()
                # Each conflict deliberately blocks later writes. New isolated
                # capture scope is required for the next operation, not a bypass.
                if kind != "delete":
                    await self.asyncTearDown()
                    await self.asyncSetUp()

    async def test_normal_cancel_rolls_back_actual_owned_outputs(self):
        for kind in ("create", "update", "move", "case", "delete"):
            with self.subTest(kind=kind):
                result, record, _, _ = await self._apply_cancel(kind, False)
                self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
                self.assertEqual(record.state, EditBatchState.ROLLED_BACK)
                expected = {} if kind == "create" else {"source.txt": b"before"}
                self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, expected)
                for path in self.root.iterdir():
                    path.unlink()

    async def test_legacy_and_malformed_receipts_never_reobserve_or_claim(self):
        for invalid in ("missing", "wrong_plan", "duplicate", "wrong_path", "mutable"):
            with self.subTest(invalid=invalid):
                plan, target, _ = self._plan("update")
                real_apply = self.editor.apply_batch

                def apply(batch):
                    result = real_apply(batch)
                    rows = result.post_identities
                    changes = {
                        "missing": {"plan_id": None, "post_identities": ()},
                        "wrong_plan": {"plan_id": "0" * 64},
                        "duplicate": {"post_identities": rows + rows},
                        "wrong_path": {"post_identities": (("other.txt", rows[0][1]),)},
                        "mutable": {"post_identities": list(rows)},
                    }
                    return replace(result, **changes[invalid])

                request = ActionRequest(invalid, "apply_workspace_edit_plan_v1", {})
                context = ActionExecutionContext(self.owner, self.owner, invalid)
                with (patch.object(self.editor, "apply_batch", side_effect=apply),
                      patch.object(self.editor, "post_identities", side_effect=AssertionError("observe"))):
                    with self.assertRaises(RuntimeError):
                        await self.capture.apply_edit_plan(context, request, plan, "stored-invalid")
                self.assertEqual(target.read_bytes(), b"after")
                records = await self.sessions.list_edit_batches(self.snapshots.workspace_fingerprint)
                self.assertEqual(records[-1].state, EditBatchState.CONFLICTED)
                if invalid != "mutable":
                    await self.asyncTearDown()
                    await self.asyncSetUp()
