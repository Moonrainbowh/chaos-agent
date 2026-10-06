"""Central approvals are bound to durable execution, not remote caller claims."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.sessions._database import _MIGRATIONS
from code_agent.sessions.workspace_models import WorkspaceLineageRecord


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "sessions.sqlite3"
        assert self.path.is_absolute() and self.path.parent == self.root
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.thread, TaskContract("inspect", TaskAuthorization.local_workspace(str(self.root))))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, "owner", 12, 34.0)
        self.binding = await self.repo.approval_binding(self.task.id, str(self.root))

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def create(self, **kw):
        args = dict(request_id=uuid.uuid4().hex, action_id="model-call", action_digest="a" * 64,
                    preview={"tool": "edit", "summary": "bounded"}, binding=self.binding, expires_at=time.time() + 60)
        args.update(kw)
        return await self.repo.create_approval_request(**args)

    async def consume(self, card, **kw):
        args = {k: card[k] for k in ("task_id", "action_digest", "state_version", "owner_instance_id")}
        args["approved"] = True
        args.update(kw)
        return await self.repo.consume_approval_request(card["request_id"], **args)

    async def test_consume_idempotent_and_conflicting_response_rejected(self):
        card = await self.create()
        approved = await self.consume(card)
        self.assertEqual(approved["status"], "approved")
        repeated = await self.consume(card)
        self.assertTrue(approved["consumed_now"])
        self.assertEqual(repeated, {**approved, "consumed_now": False})
        with self.assertRaises(ValueError):
            await self.consume(card, approved=False)
        denied = await self.create()
        self.assertEqual((await self.consume(denied, approved=False))["status"], "denied")

    async def test_expiration_is_persisted_and_cannot_be_consumed(self):
        card = await self.create()
        with patch("code_agent.sessions._approvals.time.time", return_value=card["expires_at"]):
            with self.assertRaises(ValueError):
                await self.consume(card)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "expired")

    async def test_cross_task_digest_and_owner_fail_without_consumption(self):
        card = await self.create()
        for kw in ({"task_id": "foreign"}, {"action_digest": "b" * 64}, {"owner_instance_id": "foreign"}, {"state_version": "b" * 64}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                await self.consume(card, **kw)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "pending")

    async def test_state_generation_drift_is_stale(self):
        card = await self.create()
        await self.repo.save_task_state(self.thread, replace(TaskState.empty(), code_generation=1, subject_hash="changed"))
        with self.assertRaises(ValueError):
            await self.consume(card)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "stale")

    async def test_lineage_execution_root_overrides_source_contract(self):
        worktree = self.root / "worktree"
        worktree.mkdir()
        await self.repo.create_lineage(WorkspaceLineageRecord(uuid.uuid4().hex, "repo",
            str(self.root), str(worktree), "branch", "a" * 40, owner_task_id=self.task.id))
        with self.assertRaises(ValueError):
            await self.repo.approval_binding(self.task.id, str(self.root))
        bound = await self.repo.approval_binding(self.task.id, str(worktree))
        self.assertEqual(bound["owner_instance_id"], "owner")
        await self.create(binding=bound)

    async def test_request_identity_retries_and_conflict(self):
        request = dict(request_id=uuid.uuid4().hex, expires_at=time.time() + 60)
        card = await self.create(**request)
        self.assertEqual(await self.create(**request), card)
        with self.assertRaises(ValueError):
            await self.create(**request, action_id="different-model-call")

    async def test_task_transition_invalidates_approval(self):
        card = await self.create()
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        with self.assertRaises(ValueError):
            await self.consume(card)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "stale")

    async def test_decision_requires_quiescent_owner_and_consumes_once(self):
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        with self.assertRaises(ValueError):
            await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        self.assertEqual(bound["owner_instance_id"], "")
        card = await self.create(binding=bound)
        self.assertEqual(card["kind"], "decision")
        self.assertEqual(await self.repo.invalidate_approval_requests(current_owner_instance_id="restarted"), 0)
        self.assertTrue((await self.consume(card))["consumed_now"])
        self.assertFalse((await self.consume(card))["consumed_now"])
        await self.repo.begin_task_execution(self.task.id, "new", 13, 35)
        with self.assertRaises(ValueError):
            await self.consume(card)

    async def test_decision_recovery_history_change_is_stale(self):
        from code_agent.core.models import Message
        await self.repo.transition_task(self.task.id, TaskStatus.WAITING_DECISION)
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        card = await self.create(binding=bound)
        await self.repo.append_message(self.thread, Message("user", "changed decision facts"))
        with self.assertRaises(ValueError):
            await self.consume(card)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "stale")

    async def test_atomic_task_decision_transition_and_post_version_retry(self):
        await self.repo.transition_task(self.task.id, TaskStatus.WAITING_DECISION)
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        card = await self.create(binding=bound)
        result = await self.consume(card, decision_transition="accepted_partial")
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.ACCEPTED_PARTIAL)
        self.assertEqual(result["task_status"], "accepted_partial")
        self.assertTrue(result["consumed_now"])
        repeated = await self.consume(card, decision_transition="accepted_partial")
        self.assertEqual(repeated, {**result, "consumed_now": False})
        with self.assertRaises(ValueError):
            await self.consume(card, decision_transition="failed")

    async def test_invalid_transition_rolls_back_ledger_and_task(self):
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        card = await self.create(binding=bound)
        with self.assertRaises(ValueError):
            await self.consume(card, decision_transition="accepted_partial")
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.PAUSED)
        self.assertEqual((await self.repo.list_approval_requests(self.task.id))[0]["status"], "pending")

    async def test_concurrent_state_transition_prevents_consumption(self):
        await self.repo.transition_task(self.task.id, TaskStatus.WAITING_DECISION)
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        card = await self.create(binding=bound)
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, "new", 13, 35)
        with self.assertRaises(ValueError):
            await self.consume(card, decision_transition="failed")
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.RUNNING)

    async def test_explicit_stop_transition_is_atomic(self):
        await self.repo.transition_task(self.task.id, TaskStatus.WAITING_DECISION)
        await self.repo.release_task_execution(self.task.id, "owner")
        bound = await self.repo.approval_binding(self.task.id, str(self.root), kind="decision")
        card = await self.create(binding=bound)
        stopped = await self.consume(card, decision_transition="failed")
        self.assertEqual(stopped["task_status"], "failed")
        self.assertEqual((await self.repo.load_task(self.task.id)).stop_reason, "explicit operator decision")

    async def test_restart_new_owner_cannot_reuse_pending_or_approved(self):
        pending = await self.create()
        consumed = await self.create()
        await self.consume(consumed)
        self.repo.close()
        assert self.path.parent == self.root
        self.repo = SQLiteSessionRepository(self.path)
        await self.repo.release_task_execution(self.task.id, "owner")
        await self.repo.register_task_execution(self.task.id, "new", 13, 35.0)
        for card in (pending, consumed):
            with self.assertRaises(ValueError):
                await self.consume(card)
        self.assertIn("stale", {c["status"] for c in await self.repo.list_approval_requests(self.task.id)})

    async def test_explicit_cancel_and_restart_invalidation_preserve_cards(self):
        one, two = await self.create(), await self.create()
        self.assertEqual(await self.repo.invalidate_approval_requests(request_id=one["request_id"]), 1)
        self.assertEqual(await self.repo.invalidate_approval_requests(current_owner_instance_id="new"), 1)
        self.assertEqual(len(await self.repo.list_approval_requests(self.task.id)), 2)
        with self.assertRaises(ValueError):
            await self.consume(two)

    async def test_bounded_preview_ttl_limit_and_fake_workspace(self):
        for kw in ({"preview": {"body": "x" * 8193}}, {"expires_at": time.time() + 3601}, {"expires_at": True}, {"action_digest": "x" * 64}, {"request_id": "model-call"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                await self.create(**kw)
        with self.assertRaises(ValueError):
            await self.repo.approval_binding(self.task.id, str(self.root / "foreign"))
        with self.assertRaises(ValueError):
            await self.repo.list_approval_requests(self.task.id, limit=101)
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        with self.assertRaises(ValueError):
            await self.repo.approval_binding(self.task.id, str(self.root))

    async def test_two_connections_conflicting_consumption_have_one_winner(self):
        card = await self.create()
        assert self.path.parent == self.root
        other = SQLiteSessionRepository(self.path)
        async def respond(repo, approved):
            try:
                return await repo.consume_approval_request(card["request_id"], **{k:card[k] for k in ("task_id","action_digest","state_version","owner_instance_id")}, approved=approved)
            except ValueError:
                return None
        try:
            replies = await asyncio.gather(respond(self.repo, True), respond(other, False))
            self.assertEqual(sum(r is not None for r in replies), 1)
        finally:
            other.close()

    async def test_v25_non_destructive_migration(self):
        old = self.root / "old.sqlite3"
        assert old.is_absolute() and old.parent == self.root
        with closing(sqlite3.connect(old)) as c, c:
            for version in range(1, 26):
                for sql in _MIGRATIONS[version]:
                    c.execute(sql)
                if version == 17:
                    from code_agent.sessions._database import _add_checkpoint_sequence_columns
                    _add_checkpoint_sequence_columns(c)
            c.execute("INSERT INTO threads(id,created_at,updated_at) VALUES ('old','2026-01-01','2026-01-01')")
            c.execute("PRAGMA user_version=25")
        assert old.parent == self.root
        migrated = SQLiteSessionRepository(old)
        try:
            with closing(sqlite3.connect(old)) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 26)
                self.assertEqual(c.execute("SELECT id FROM threads").fetchone()[0], "old")
                self.assertEqual(c.execute("SELECT count(*) FROM approval_requests").fetchone()[0], 0)
        finally:
            migrated.close()
