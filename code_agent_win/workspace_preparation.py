from __future__ import annotations

import asyncio
from typing import TypeVar

from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.workspace_models import WorkspaceLineageRecord
from code_agent.workspace._worktree_leases import UnclaimedWorktreeLease
from code_agent.workspace.worktrees import WorktreeManager

from code_agent_win.workspace_models import TaskWorkspace


_T = TypeVar("_T")


class PreparedWorkspaceCoordinator:
    """Own one-time worktree leases until lineage persistence claims them."""

    def __init__(self, sessions: object, worktrees: WorktreeManager) -> None:
        self.sessions = sessions
        self.worktrees = worktrees
        self.prepared: dict[str, UnclaimedWorktreeLease] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, lease: UnclaimedWorktreeLease) -> TaskWorkspace:
        worktree = lease.worktree
        if worktree.lineage_id in self.prepared:
            raise RuntimeError("worktree lineage is already prepared")
        self.prepared[worktree.lineage_id] = lease
        self._locks[worktree.lineage_id] = asyncio.Lock()
        return TaskWorkspace(
            worktree.lineage_id,
            worktree.source_root,
            worktree.root,
            worktree.branch_name,
        )

    async def create_lineage(self, workspace: TaskWorkspace, task_id: str) -> None:
        lock = self._lock_for(workspace)
        assert lock is not None
        async with lock:
            await self._create_lineage_locked(workspace, task_id)

    async def _create_lineage_locked(
        self, workspace: TaskWorkspace, task_id: str
    ) -> None:
        lease = self._require(workspace)
        assert lease is not None
        worktree = lease.worktree
        record = WorkspaceLineageRecord(
            worktree.lineage_id,
            repository_id=worktree.repository_id,
            source_root=str(worktree.source_root),
            worktree_root=str(worktree.root),
            branch_name=worktree.branch_name,
            head_commit=worktree.head_commit,
            owner_task_id=task_id,
        )
        try:
            await self.sessions.create_lineage(record)
        except BaseException as primary:
            await self._resolve_failed_write(record, lease, primary)
            raise
        self._disable_cleanup(lease, None)

    async def abort(self, workspace: TaskWorkspace) -> bool:
        lock = self._lock_for(workspace, required=False)
        if lock is None:
            return False
        async with lock:
            return await self._abort_locked(workspace)

    async def _abort_locked(self, workspace: TaskWorkspace) -> bool:
        lease = self._require(workspace, required=False)
        if lease is None:
            return False
        worker = asyncio.create_task(
            asyncio.to_thread(self.worktrees.discard_unclaimed, lease)
        )
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            try:
                await _settle(worker)
            except BaseException as cleanup:
                _add_note(cancellation, f"prepared worktree cleanup failed: {cleanup}")
            else:
                self._pop(lease)
            raise cancellation
        self._pop(lease)
        return True

    async def _resolve_failed_write(
        self,
        record: WorkspaceLineageRecord,
        lease: UnclaimedWorktreeLease,
        primary: BaseException,
    ) -> None:
        query = asyncio.create_task(self.sessions.load_lineage(record.id))
        try:
            persisted = await _settle(query)
        except SessionNotFound:
            try:
                await self._discard_settled(lease)
            except BaseException as cleanup:
                _add_note(primary, f"unclaimed worktree cleanup failed: {cleanup}")
            return
        except BaseException as verification:
            self._disable_cleanup(lease, primary)
            _add_note(
                primary,
                "lineage persistence could not be verified; destructive cleanup "
                f"was disabled: {verification}",
            )
            return
        self._disable_cleanup(lease, primary)
        if persisted != record:
            _add_note(
                primary,
                "persisted lineage differs from the attempted record; cleanup "
                "was disabled",
            )

    async def _discard_settled(self, lease: UnclaimedWorktreeLease) -> None:
        worker = asyncio.create_task(
            asyncio.to_thread(self.worktrees.discard_unclaimed, lease)
        )
        await _settle(worker)
        self._pop(lease)

    def _disable_cleanup(
        self,
        lease: UnclaimedWorktreeLease,
        primary: BaseException | None,
    ) -> None:
        self._pop(lease)
        try:
            self.worktrees.claim_unclaimed(lease)
        except Exception as error:
            if primary is None:
                raise
            _add_note(primary, f"worktree lease claim failed: {error}")

    def _require(
        self,
        workspace: TaskWorkspace,
        *,
        required: bool = True,
    ) -> UnclaimedWorktreeLease | None:
        if type(workspace) is not TaskWorkspace:
            raise TypeError("workspace must be a TaskWorkspace")
        lease = self.prepared.get(workspace.lineage_id)
        if lease is None:
            if required:
                raise RuntimeError("workspace is not awaiting lineage persistence")
            return None
        worktree = lease.worktree
        actual = (
            workspace.source_root,
            workspace.worktree_root,
            workspace.branch_name,
        )
        expected = (worktree.source_root, worktree.root, worktree.branch_name)
        if actual != expected:
            raise RuntimeError("prepared workspace does not match its worktree lease")
        return lease

    def _lock_for(
        self,
        workspace: TaskWorkspace,
        *,
        required: bool = True,
    ) -> asyncio.Lock | None:
        if type(workspace) is not TaskWorkspace:
            raise TypeError("workspace must be a TaskWorkspace")
        lock = self._locks.get(workspace.lineage_id)
        if lock is None and required:
            raise RuntimeError("workspace is not awaiting lineage persistence")
        return lock

    def _pop(self, lease: UnclaimedWorktreeLease) -> None:
        lineage_id = lease.worktree.lineage_id
        if self.prepared.get(lineage_id) is lease:
            self.prepared.pop(lineage_id)
            self._locks.pop(lineage_id, None)


async def _settle(task: asyncio.Task[_T]) -> _T:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


def _add_note(error: BaseException, note: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
