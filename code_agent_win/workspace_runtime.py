from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Sequence, TypeVar

from code_agent.checkpoints.locks import LineageLockPool
from code_agent.interfaces.checkpoint_control import CheckpointControl
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from code_agent.runtime.models import PowerShellRuntimeInfo
from code_agent.sessions.workspace_models import RewindOperationStatus
from code_agent.sessions.errors import SessionNotFound
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace._worktree_leases import UnclaimedWorktreeLease
from code_agent.workspace.worktrees import WorktreeManager

from code_agent_win.windows_storage_paths import resolve_managed_storage_root
from code_agent_win.workspace_preparation import PreparedWorkspaceCoordinator
from code_agent_win.workspace_service_factory import build_workspace_services
from code_agent_win.workspace_models import TaskWorkspace, WorkspaceServices
from code_agent_win.workspace_checkpoint_runtime import (
    CheckpointRouter,
    StableQuiescer,
    checkpoint_control,
    noop_invalidate_verification,
)
from code_agent_win.workspace_startup_recovery import (
    recover_workspace_edit_batches,
)


_T = TypeVar("_T")


class ManagedWorkspaceRuntime:
    def __init__(
        self,
        sessions: object,
        storage_root: Path,
        *,
        snapshot_read_fallback_roots: Sequence[Path] = (),
        allow_sensitive_paths: bool = False,
        powershell: PowerShellRuntimeResolver | None = None,
    ) -> None:
        if not isinstance(allow_sensitive_paths, bool):
            raise TypeError("allow_sensitive_paths must be a bool")
        canonical_storage = resolve_managed_storage_root(storage_root)
        worktrees_root = canonical_storage / "worktrees"
        canonical_storage.mkdir(parents=True, exist_ok=True)
        self._sessions = sessions
        self.storage_root = canonical_storage
        self.snapshot_read_fallback_roots = tuple(snapshot_read_fallback_roots)
        self.allow_sensitive_paths = allow_sensitive_paths
        self.powershell = powershell or PowerShellRuntimeResolver()
        worktrees_root.mkdir(parents=True, exist_ok=True)
        self._worktrees = WorktreeManager(worktrees_root)
        self._preparation = PreparedWorkspaceCoordinator(sessions, self._worktrees)
        self._locks = LineageLockPool()
        self._services: dict[str, WorkspaceServices] = {}
        self._thread_roots: dict[str, Path] = {}
        self._task_roots: dict[str, Path] = {}
        self._prepared = self._preparation.prepared
        self._verification_invalidator = noop_invalidate_verification
        self._quiescer = StableQuiescer()
        self._startup_complete = False
        self._startup_lock = asyncio.Lock()
        self._mutations: object | None = None
        self._mutation_source_root: Path | None = None

    def powershell_info(self) -> PowerShellRuntimeInfo:
        return self.powershell.resolve()

    async def prepare_task(self, source_root: Path, task_id: str) -> TaskWorkspace:
        source = source_root.resolve()
        lineage_id = uuid.uuid4().hex
        branch_name = f"codex/task-{lineage_id}"
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._prepare_task_sync, source, lineage_id, branch_name
            )
        )
        try:
            lease = await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            await self._finish_cancelled_prepare(worker, cancellation)
            raise
        return self._preparation.register(lease)

    async def create_lineage(self, workspace: TaskWorkspace, task_id: str) -> None:
        await self._preparation.create_lineage(workspace, task_id)
        self.bind_task(task_id, workspace.worktree_root)

    async def abort_prepared_task(self, workspace: TaskWorkspace) -> bool:
        return await self._preparation.abort(workspace)

    def bind_thread(self, thread_id: str, root: Path) -> None:
        self._thread_roots[thread_id] = root.resolve()

    def bind_task(self, task_id: str, root: Path) -> None:
        self._task_roots[task_id] = root.resolve()

    def root_for_thread(self, thread_id: str) -> Path | None:
        return self._thread_roots.get(thread_id)

    def root_for_task(self, task_id: str) -> Path:
        return self._task_roots[task_id]

    async def startup(self) -> tuple[object, ...]:
        async with self._startup_lock:
            if self._startup_complete:
                return ()
            await self.hydrate_bindings()
            batches = await self.recover_edit_batches()
            rewinds = await self.recover_pending()
            await self.hydrate_bindings()
            self._startup_complete = True
            return batches + rewinds

    def configure_mutations(self, mutations: object, source_root: Path) -> None:
        if self._startup_complete:
            raise RuntimeError("workspace runtime has already started")
        if not callable(getattr(mutations, "for_services", None)):
            raise TypeError("mutations must provide for_services")
        if not isinstance(source_root, Path):
            raise TypeError("source_root must be a Path")
        self._mutations = mutations
        self._mutation_source_root = source_root.resolve()

    async def recover_edit_batches(self) -> tuple[object, ...]:
        if self._mutations is None or self._mutation_source_root is None:
            return ()
        return await recover_workspace_edit_batches(
            self, self._mutations, self._mutation_source_root
        )

    async def hydrate_bindings(self) -> None:
        for task in await self._sessions.list_tasks(include_terminal=True):
            await self.bind_persisted_task(task.id)

    async def bind_persisted_task(self, task_id: str) -> None:
        try:
            task = await self._sessions.load_task(task_id)
            lineage = await self._sessions.load_lineage_for_task(task_id)
        except SessionNotFound:
            return
        root = Path(lineage.worktree_root)
        self.bind_task(task.id, root)
        self.bind_thread(task.thread_id, root)

    def services(self, workspace: TaskWorkspace) -> WorkspaceServices:
        return self.services_for_root(workspace.worktree_root)

    def services_for_root(self, root: Path) -> WorkspaceServices:
        resolved = root.resolve()
        key = str(resolved).casefold()
        if key not in self._services:
            self._services[key] = self._build_services(resolved)
        return self._services[key]

    def close(self) -> None:
        for service in tuple(self._services.values()):
            service.repo_index.close()
        self._services.clear()

    async def recover_pending(self) -> tuple[object, ...]:
        results: list[object] = []
        for operation in await self._sessions.pending_rewinds():
            if operation.status is not RewindOperationStatus.PENDING:
                continue
            lineage = await self._sessions.load_lineage(operation.lineage_id)
            control = self.services_for_root(Path(lineage.worktree_root)).checkpoints
            if control is not None:
                result = await control.recover_rewind(operation.id)
                results.append(result)
                if result.replacement_task_id is not None:
                    await self.bind_persisted_task(result.replacement_task_id)
        return tuple(results)

    def checkpoint_control(self) -> CheckpointControl:
        return CheckpointRouter(self)

    def set_quiescer(self, callback: object) -> None:
        self._quiescer.bind(callback)

    async def quiesce_task(self, task_id: str) -> None:
        await self._quiescer(task_id)

    async def checkpoint_available(self, task_id: str) -> bool:
        try:
            lineage = await self._sessions.load_lineage_for_task(task_id)
        except SessionNotFound:
            return False
        return self.services_for_root(Path(lineage.worktree_root)).checkpoints is not None

    def set_verification_invalidator(self, callback: object) -> None:
        self._verification_invalidator = callback

    def _prepare_task_sync(
        self, source: Path, lineage_id: str, branch_name: str
    ) -> UnclaimedWorktreeLease:
        lease = self._worktrees.create_unclaimed(
            source, lineage_id, branch_name
        )
        managed = lease.worktree
        try:
            self._seed_source_changes(source, managed.root)
        except BaseException as error:
            try:
                self._worktrees.discard_unclaimed(lease)
            except Exception as cleanup_error:
                if hasattr(error, "add_note"):
                    error.add_note(
                        f"unclaimed worktree cleanup failed: {cleanup_error}"
                    )
            raise
        return lease

    async def _finish_cancelled_prepare(
        self,
        worker: asyncio.Task[UnclaimedWorktreeLease],
        cancellation: asyncio.CancelledError,
    ) -> None:
        try:
            lease = await self._settle_shielded(worker)
        except BaseException as error:
            self._add_note(
                cancellation, f"cancelled preparation settled with: {error}"
            )
            return
        cleanup = asyncio.create_task(
            asyncio.to_thread(self._worktrees.discard_unclaimed, lease)
        )
        try:
            await self._settle_shielded(cleanup)
        except BaseException as error:
            self._add_note(
                cancellation, f"unclaimed worktree cleanup failed: {error}"
            )

    @staticmethod
    def _add_note(error: BaseException, note: str) -> None:
        add_note = getattr(error, "add_note", None)
        if callable(add_note):
            add_note(note)

    @staticmethod
    async def _settle_shielded(worker: asyncio.Task[_T]) -> _T:
        while True:
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                if worker.done():
                    return worker.result()

    def _seed_source_changes(self, source_root: Path, target_root: Path) -> None:
        paths = GitWorkspace(source_root).changed_snapshot_paths()
        editor = WorkspaceEditor(
            WorkspacePathGuard(
                source_root, allow_sensitive=self.allow_sensitive_paths
            )
        )
        snapshot = editor.snapshot(paths)
        target = WorkspaceEditor(
            WorkspacePathGuard(
                target_root, allow_sensitive=self.allow_sensitive_paths
            )
        )
        target.restore(snapshot)

    def _build_services(self, root: Path) -> WorkspaceServices:
        service = build_workspace_services(
            root,
            allow_sensitive_paths=self.allow_sensitive_paths,
            powershell=self.powershell,
        )
        if service.git is not None:
            service.checkpoints = self._checkpoint_control(service)
        return service

    def _checkpoint_control(self, service: WorkspaceServices) -> CheckpointControl:
        return checkpoint_control(
            self._sessions,
            self._locks,
            service,
            self.storage_root / "snapshots",
            self.quiesce_task,
            self._verification_invalidator,
            snapshot_read_fallback_roots=self.snapshot_read_fallback_roots,
        )
