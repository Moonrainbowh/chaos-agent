from __future__ import annotations

import asyncio
import hashlib
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.rewind_models import (
    CoverageToken,
    RewindBaseline,
    RewindCoverageState,
    RewindMutationStatus,
)
from code_agent.workspace.edits import EditPlan, SnapshotEntry, WorkspaceSnapshot
from code_agent.workspace.rewind_state import PreparedEditState, WorkspaceFileState
from code_agent.workspace.snapshot_store import SnapshotHandle
from code_agent_win.rewind_capture import RewindCaptureCoordinator


FINGERPRINT = "a" * 64
BEFORE = hashlib.sha256(b"before").hexdigest()
AFTER = hashlib.sha256(b"after").hexdigest()


class _Lease:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def release(self) -> None:
        self.calls.append("gate.release")


class _Gate:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def acquire(self) -> _Lease:
        self.calls.append("gate.acquire")
        return _Lease(self.calls)


class _Sessions:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.state = RewindCoverageState.ACTIVE
        self.fail: str | None = None
        self.block: dict[str, asyncio.Event] = {}
        self.records: list[object] = []
        self.finished: RewindMutationStatus | None = None

    async def _call(self, name: str, value: object) -> object:
        self.calls.append(f"sessions.{name}")
        event = self.block.get(name)
        if event is not None:
            await event.wait()
        if self.fail == name:
            raise RuntimeError(name)
        return value

    async def ensure_rewind_coverage(self, fingerprint: str) -> object:
        assert fingerprint == FINGERPRINT
        return await self._call(
            "ensure", SimpleNamespace(token=CoverageToken(fingerprint, 1), state=self.state)
        )

    async def prepare_rewind_mutation(self, request: object) -> object:
        self.records.append(request)
        return await self._call(
            "prepare",
            SimpleNamespace(mutation_id="mutation-1", status=RewindMutationStatus.PREPARED),
        )

    async def complete_rewind_mutation(self, mutation_id: str) -> object:
        result = await self._call(
            "complete", SimpleNamespace(
                mutation_id=mutation_id, status=RewindMutationStatus.COMPLETED,
            ),
        )
        self.finished = result.status
        return result

    async def abort_rewind_mutation(self, mutation_id: str) -> object:
        result = await self._call(
            "abort", SimpleNamespace(
                mutation_id=mutation_id, status=RewindMutationStatus.ABORTED,
            ),
        )
        self.finished = result.status
        return result

    async def record_rewind_gap(self, request: object) -> object:
        self.records.append(request)
        return await self._call("gap", SimpleNamespace(status=RewindMutationStatus.GAP))


class _Editor:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.error: BaseException | None = None

    def apply(self, plan: EditPlan) -> None:
        self.calls.append("editor.apply")
        if self.error is not None:
            raise self.error


class _Snapshots:
    workspace_fingerprint = FINGERPRINT

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.fail = False

    def save(self, snapshot: WorkspaceSnapshot) -> SnapshotHandle:
        self.calls.append("snapshot.save")
        if self.fail:
            raise RuntimeError("save")
        return SnapshotHandle("1" * 32, "2" * 64, ("note.txt",), 6)


def _prepared() -> PreparedEditState:
    before = WorkspaceFileState("note.txt", True, BEFORE, 6)
    after = WorkspaceFileState("note.txt", True, AFTER, 5)
    return PreparedEditState(
        WorkspaceSnapshot((SnapshotEntry("note.txt", b"before", True),)),
        before,
        after,
    )


def _blocking_observation(calls: list[str], state: WorkspaceFileState):
    started, finish = threading.Event(), threading.Event()

    def observe(editor: object, paths: tuple[str, ...]):
        calls.append("workspace.observe")
        started.set()
        finish.wait(2)
        return (state,)

    return started, finish, observe


class CaptureHarness:
    def build(self, baseline: RewindBaseline = RewindBaseline.UNKNOWN) -> None:
        self.calls: list[str] = []
        self.sessions = _Sessions(self.calls)
        self.editor = _Editor(self.calls)
        self.snapshots = _Snapshots(self.calls)
        self.coordinator = RewindCaptureCoordinator(
            self.sessions, self.editor, self.snapshots, _Gate(self.calls),
            existing_baseline=baseline,
        )
        self.context = ActionExecutionContext("owner", "origin", "request", "task", "parent")
        self.request = ActionRequest("request", "write_file", {"path": "note.txt"})
        self.plan = EditPlan("note.txt", BEFORE, "after", "diff", True)
        self.observed = _prepared().after

    def patches(self):
        def prepare(editor: object, plan: EditPlan) -> PreparedEditState:
            self.calls.append("workspace.prepare")
            return _prepared()

        def observe(editor: object, paths: tuple[str, ...]):
            self.calls.append("workspace.observe")
            return (self.observed,)

        return patch.multiple(
            "code_agent_win.rewind_capture",
            prepare_edit_state=prepare,
            observe_file_states=observe,
        )


class RewindCaptureTests(CaptureHarness, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.build()

    async def test_known_edit_has_exact_durable_order(self) -> None:
        with self.patches():
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertEqual(self.calls, [
            "gate.acquire", "sessions.ensure", "workspace.prepare",
            "snapshot.save", "sessions.prepare", "editor.apply",
            "workspace.observe", "sessions.complete", "gate.release",
        ])
        record = self.sessions.records[0]
        self.assertEqual(record.parent_request_id, "parent")
        self.assertEqual(dict(record.snapshot_handle)["identifier"], "1" * 32)

    async def test_snapshot_or_prepare_failure_causes_zero_file_write(self) -> None:
        self.snapshots.fail = True
        with self.patches(), self.assertRaises(RuntimeError):
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertNotIn("editor.apply", self.calls)
        self.assertEqual(self.calls[-1], "gate.release")

    async def test_apply_failure_aborts_only_when_current_state_is_preimage(self) -> None:
        self.editor.error = RuntimeError("apply")
        self.observed = _prepared().before
        with self.patches(), self.assertRaises(RuntimeError):
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertIn("sessions.abort", self.calls)
        self.assertNotIn("sessions.complete", self.calls)

    async def test_postimage_after_apply_error_completes_but_returns_error(self) -> None:
        self.editor.error = RuntimeError("apply")
        with self.patches(), self.assertRaises(RuntimeError):
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertIn("sessions.complete", self.calls)

    async def test_third_state_after_apply_error_remains_prepared(self) -> None:
        self.editor.error = RuntimeError("apply")
        self.observed = WorkspaceFileState("note.txt", True, "3" * 64, 7)
        with self.patches(), self.assertRaises(RuntimeError):
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertNotIn("sessions.abort", self.calls)
        self.assertNotIn("sessions.complete", self.calls)

    async def test_completion_failure_leaves_prepared_and_returns_error(self) -> None:
        self.sessions.fail = "complete"
        with self.patches(), self.assertRaises(RuntimeError):
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertNotIn("sessions.abort", self.calls)

    async def test_invalidated_workspace_continues_typed_edit_as_gap(self) -> None:
        self.sessions.state = RewindCoverageState.INVALIDATED
        with self.patches():
            await self.coordinator.apply_edit(self.context, self.request, self.plan)
        self.assertEqual(self.calls, [
            "gate.acquire", "sessions.ensure", "sessions.gap",
            "editor.apply", "gate.release",
        ])
        self.assertEqual(self.sessions.records[0].reason, "coverage-already-invalidated")

    async def test_capture_rejects_mismatched_request_identity_before_side_effects(self) -> None:
        mismatch = ActionExecutionContext("owner", "origin", "other")
        with self.assertRaises(ValueError):
            await self.coordinator.apply_edit(mismatch, self.request, self.plan)
        self.assertEqual(self.calls, [])

    async def test_capture_path_requires_execution_context(self) -> None:
        with self.assertRaises(TypeError):
            await self.coordinator.apply_edit(None, self.request, self.plan)  # type: ignore[arg-type]
        self.assertEqual(self.calls, [])

    async def test_record_gap_is_durable_before_return(self) -> None:
        await self.coordinator.record_gap(self.context, self.request, "unknown-writer")
        self.assertEqual(self.calls, [
            "gate.acquire", "sessions.ensure", "sessions.gap", "gate.release",
        ])
        self.assertEqual(self.sessions.records[0].reason, "unknown-writer")

    async def test_cancelled_ensure_settles_before_gate_release(self) -> None:
        blocker = self.sessions.block["ensure"] = asyncio.Event()
        task = asyncio.create_task(
            self.coordinator.record_gap(self.context, self.request, "unknown-writer")
        )
        await asyncio.sleep(0)
        task.cancel()
        blocker.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(self.calls.index("sessions.ensure"), self.calls.index("gate.release"))

    async def test_cancelled_gap_settles_before_gate_release(self) -> None:
        blocker = self.sessions.block["gap"] = asyncio.Event()
        task = asyncio.create_task(
            self.coordinator.record_gap(self.context, self.request, "unknown-writer")
        )
        while "sessions.gap" not in self.calls:
            await asyncio.sleep(0)
        task.cancel()
        blocker.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(self.calls.index("sessions.gap"), self.calls.index("gate.release"))

    async def test_post_observe_cancellation_reconciles_before_release(self) -> None:
        started, finish, observe = _blocking_observation(
            self.calls, _prepared().after)
        with patch.multiple(
            "code_agent_win.rewind_capture",
            prepare_edit_state=lambda editor, plan: _prepared(),
            observe_file_states=observe,
        ):
            task = asyncio.create_task(
                self.coordinator.apply_edit(self.context, self.request, self.plan)
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIs(self.sessions.finished, RewindMutationStatus.COMPLETED)
        self.assertLess(self.calls.index("sessions.complete"), self.calls.index("gate.release"))
