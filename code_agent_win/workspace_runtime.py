from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from code_agent.checkpoints.locks import LineageLockPool
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.interfaces.checkpoint_control import CheckpointControl
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.sessions.workspace_models import (
    RewindOperationStatus,
    WorkspaceLineageRecord,
)
from code_agent.sessions.errors import SessionNotFound
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.worktrees import WorktreeManager

from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.tool_support import discover_git_workspace
from code_agent_win.workspace_checkpoint_runtime import (
    CheckpointRouter,
    StableQuiescer,
    checkpoint_control,
)


@dataclass(frozen=True)
class TaskWorkspace:
    lineage_id: str
    source_root: Path
    worktree_root: Path
    branch_name: str


@dataclass
class WorkspaceServices:
    root: Path
    guard: WorkspacePathGuard
    files: WorkspaceFiles
    git: GitWorkspace | None
    repo_index: RepoIndexService
    runtime: WindowsLocalRuntime
    verification: LocalVerificationAdapter
    checkpoints: CheckpointControl | None = None


class ManagedWorkspaceRuntime:
    def __init__(self, sessions: object, storage_root: Path) -> None:
        storage_root.mkdir(parents=True, exist_ok=True)
        self._sessions = sessions
        self.storage_root = storage_root.resolve()
        (self.storage_root / "worktrees").mkdir(parents=True, exist_ok=True)
        self._worktrees = WorktreeManager(self.storage_root / "worktrees")
        self._locks = LineageLockPool()
        self._services: dict[str, WorkspaceServices] = {}
        self._thread_roots: dict[str, Path] = {}
        self._task_roots: dict[str, Path] = {}
        self._prepared: dict[str, tuple[str, str]] = {}
        self._verification_invalidator = _noop_invalidate_verification
        self._quiescer = StableQuiescer()
        self._startup_complete = False

    async def prepare_task(self, source_root: Path, task_id: str) -> TaskWorkspace:
        source = source_root.resolve()
        lineage_id = uuid.uuid4().hex
        branch_name = f"codex/task-{lineage_id}"
        managed = await asyncio.to_thread(
            self._worktrees.create, source, lineage_id, branch_name
        )
        await self._seed_source_changes(source, managed.root)
        self._prepared[lineage_id] = (
            managed.repository_id,
            managed.head_commit,
        )
        return TaskWorkspace(lineage_id, source, managed.root, managed.branch_name)

    async def create_lineage(self, workspace: TaskWorkspace, task_id: str) -> None:
        repository_id, head_commit = self._prepared.pop(workspace.lineage_id)
        record = WorkspaceLineageRecord(
            workspace.lineage_id,
            repository_id=repository_id,
            source_root=str(workspace.source_root),
            worktree_root=str(workspace.worktree_root),
            branch_name=workspace.branch_name,
            head_commit=head_commit,
            owner_task_id=task_id,
        )
        await self._sessions.create_lineage(record)
        self.bind_task(task_id, workspace.worktree_root)

    def bind_thread(self, thread_id: str, root: Path) -> None:
        self._thread_roots[thread_id] = root.resolve()

    def bind_task(self, task_id: str, root: Path) -> None:
        self._task_roots[task_id] = root.resolve()

    def root_for_thread(self, thread_id: str) -> Path | None:
        return self._thread_roots.get(thread_id)

    def root_for_task(self, task_id: str) -> Path:
        return self._task_roots[task_id]

    async def startup(self) -> tuple[object, ...]:
        if self._startup_complete:
            return ()
        await self.hydrate_bindings()
        results = await self.recover_pending()
        await self.hydrate_bindings()
        self._startup_complete = True
        return results

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

    async def recover_pending(self) -> tuple[object, ...]:
        results: list[object] = []
        for operation in await self._sessions.pending_rewinds():
            if operation.status is not RewindOperationStatus.PENDING:
                continue
            lineage = await self._sessions.load_lineage(operation.lineage_id)
            control = self.services_for_root(Path(lineage.worktree_root)).checkpoints
            if control is not None:
                async with control._rewind.locks.for_lineage(operation.lineage_id):
                    result = await control._rewind.recovery._recover_one(operation)
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

    async def _seed_source_changes(self, source_root: Path, target_root: Path) -> None:
        paths = await asyncio.to_thread(GitWorkspace(source_root).snapshot_paths)
        snapshot = await asyncio.to_thread(
            WorkspaceEditor(WorkspacePathGuard(source_root)).snapshot, paths
        )
        await asyncio.to_thread(
            WorkspaceEditor(WorkspacePathGuard(target_root)).restore, snapshot
        )

    def _build_services(self, root: Path) -> WorkspaceServices:
        guard = WorkspacePathGuard(root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
        git = discover_git_workspace(root)
        repo_index = RepoIndexService(files, scan_file=RepoFileScanner(files).scan)
        service = WorkspaceServices(
            root, guard, files, git, repo_index,
            WindowsLocalRuntime(root), LocalVerificationAdapter(root),
        )
        if git is not None:
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
        )


class TaskScopedDispatcher:
    def __init__(
        self,
        runtime: ManagedWorkspaceRuntime,
        source_services: WorkspaceServices,
        policy: ActionPolicy,
        approvals: object,
        **dependencies: object,
    ) -> None:
        self._runtime, self._source = runtime, source_services
        self.policy, self.approvals = policy, approvals
        self.mcp = dependencies.get("mcp")
        self.plugins = dependencies.get("plugins")
        self.threads = dependencies.get("threads")
        self.caller_thread = dependencies.get("caller_thread")
        self.capture = dependencies.get("capture")
        self.subagents = None
        self.interactive = False

    @property
    def editor(self) -> WorkspaceEditor:
        return WorkspaceEditor(self._source.guard)

    def tools(self) -> Sequence[ToolDefinition]:
        return self._dispatcher(self._source).tools()

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: object,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: object | None = None,
    ) -> ActionResult:
        services = self._runtime.services_for_root(
            self._authorized_root(task_authorization)
        )
        return await self._dispatcher(services).dispatch(
            request, cancellation, task_authorization,
            execution_context=execution_context,
        )

    def _authorized_root(self, authorization: TaskAuthorization | None) -> Path:
        return (
            self._source.root
            if authorization is None
            else Path(authorization.workspace_root).resolve()
        )

    def _dispatcher(self, service: WorkspaceServices) -> RootActionDispatcher:
        dispatcher = RootActionDispatcher(
            service.files,
            WorkspaceEditor(service.guard),
            self._policy_for(service.root),
            self.approvals,
            git=service.git,
            runtime=service.runtime,
            verification=service.verification,
            mcp=self.mcp,
            plugins=self.plugins,
            subagents=self.subagents,
            threads=self.threads,
            capture=self.capture,
            caller_thread=self.caller_thread,
            invalidate_cache=lambda paths: _invalidate(service, paths),
        )
        dispatcher.interactive = self.interactive
        return dispatcher

    def _policy_for(self, root: Path) -> ActionPolicy:
        return ActionPolicy(
            PolicyConfig(
                self.policy.config.approval_mode,
                workspace_root=root,
                mcp_risks=dict(self.policy.config.mcp_risks),
            )
        )


def _invalidate(service: WorkspaceServices, paths: Sequence[str]) -> None:
    service.repo_index.invalidate(paths)
    service.files.invalidate_inventory()


async def _noop_invalidate_verification(task_id: str, replacement_task_id: str | None) -> None:
    return None
