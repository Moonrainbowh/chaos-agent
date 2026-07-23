from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Mapping, Sequence

from code_agent.checkpoints.rewind import RewindCoordinator
from code_agent.checkpoints.service import CheckpointService
from code_agent.interfaces.checkpoint_control import CheckpointControl
from code_agent.sessions.errors import SessionCorruptionError, SessionNotFound
from code_agent.workspace.edits import WorkspaceEditor, build_restore_snapshot
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.inventory import WorkspaceInventory
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import ContentAddressedSnapshotStore


class StableQuiescer:
    def __init__(self) -> None:
        self._callback: object | None = None

    def bind(self, callback: object) -> None:
        if not callable(callback):
            raise TypeError("workspace quiescer must be callable")
        self._callback = callback

    async def __call__(self, task_id: str) -> None:
        callback = self._callback
        if callback is None:
            raise RuntimeError("workspace quiescer is not bound")
        result = callback(task_id)
        if not inspect.isawaitable(result):
            raise TypeError("workspace quiescer must return an awaitable")
        await result


def checkpoint_control(
    sessions: object,
    locks: object,
    service: object,
    store_root: Path,
    quiesce: object,
    invalidate_verification: object,
) -> CheckpointControl:
    workspace = _CheckpointWorkspace(
        service.root, service.guard, service.git, store_root
    )
    checkpoints = _ListedCheckpointService(
        sessions, workspace, quiesce, locks
    )
    rewind = RewindCoordinator(
        sessions,
        workspace,
        checkpoints,
        quiesce,
        invalidate_cache=lambda paths: _invalidate(service, paths),
        invalidate_verification=invalidate_verification,
    )
    return CheckpointControl(checkpoints, rewind)


class CheckpointRouter:
    def __init__(self, runtime: object) -> None:
        self._runtime = runtime

    async def list(self, task_id: str):
        control = await self._control_for_task(task_id)
        return await control.list(task_id)

    async def create(self, task_id: str, label: str):
        control = await self._control_for_task(task_id)
        return await control.create(task_id, label)

    async def preview_rewind(self, task_id: str, checkpoint_id: str, mode="code"):
        control = await self._control_for_task(task_id)
        return await control.preview_rewind(task_id, checkpoint_id, mode)

    async def execute_rewind(self, preview: object, *, confirmed: bool):
        lineage = await self._runtime._sessions.load_lineage(preview.lineage_id)
        control = self._runtime.services_for_root(Path(lineage.worktree_root)).checkpoints
        if control is None:
            raise RuntimeError("checkpoint control is unavailable")
        result = await control.execute_rewind(preview, confirmed=confirmed)
        if result.replacement_task_id is not None:
            await self._runtime.bind_persisted_task(result.replacement_task_id)
        return result

    async def _control_for_task(self, task_id: str) -> CheckpointControl:
        lineage = await self._runtime._sessions.load_lineage_for_task(task_id)
        control = self._runtime.services_for_root(Path(lineage.worktree_root)).checkpoints
        if control is None:
            raise RuntimeError("checkpoint control is unavailable")
        return control


class _CheckpointWorkspace:
    def __init__(
        self,
        root: Path,
        guard: WorkspacePathGuard,
        git: GitWorkspace | None,
        store_root: Path,
    ) -> None:
        if git is None:
            raise RuntimeError("checkpoint workspace requires Git")
        self._root, self._guard, self._git = root, guard, git
        self._editor = WorkspaceEditor(guard)
        self._store = ContentAddressedSnapshotStore(store_root)

    async def inventory(self) -> WorkspaceInventory:
        return await asyncio.to_thread(
            WorkspaceInventory.capture, self._root, self._guard, self._git
        )

    async def snapshot(self, paths: tuple[str, ...]):
        return await asyncio.to_thread(self._editor.snapshot, paths)

    async def store(self, snapshot: object, modes: Mapping[str, int]):
        return await asyncio.to_thread(self._store.put, snapshot, modes)

    async def materialize(self, snapshot: object):
        return await asyncio.to_thread(self._store.materialize, snapshot.manifest)

    async def restore(
        self, checkpoint_id: str, snapshot: object, current_paths: tuple[str, ...]
    ) -> None:
        restore = build_restore_snapshot(current_paths, snapshot.snapshot)
        await asyncio.to_thread(self._editor.restore, restore)


class _ListedCheckpointService(CheckpointService):
    async def list(self, task_id: str):
        task = await self.sessions.load_task(task_id)
        records = await self.sessions.list_checkpoints(task.thread_id)
        listed = []
        for record in records:
            try:
                await self.sessions.load_checkpoint_cursor(record.id)
            except (SessionNotFound, SessionCorruptionError):
                continue
            listed.append(record)
        return tuple(listed)


def _invalidate(service: object, paths: Sequence[str]) -> None:
    service.repo_index.invalidate(paths)
    service.files.invalidate_inventory()
