from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from code_agent.web_access import WebAccessService
from typing import TYPE_CHECKING, Iterator, Sequence

from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import WorkspaceEditor

from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.verification_mode import structured_verification_enabled
from chaos_agent.action_metrics import ActionMetricsCollector
from chaos_agent.action_resolution import resolve_plugin_action
from chaos_agent.workspace_mutation_pool import WorkspaceMutationPool
from chaos_agent.workspace_models import WorkspaceServices

if TYPE_CHECKING:
    from chaos_agent.workspace_runtime import ManagedWorkspaceRuntime


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
        self.web_access = dependencies.get("web_access") or WebAccessService()
        self.mcp = dependencies.get("mcp")
        self.plugins = dependencies.get("plugins")
        self.threads = dependencies.get("threads")
        self.peers = dependencies.get("peers")
        self.caller_thread = dependencies.get("caller_thread")
        self.capture = dependencies.get("capture")
        self.metrics = dependencies.get("metrics") or ActionMetricsCollector()
        self.process_rules = dependencies.get("process_rules")
        self._permission_override: ContextVar[tuple[ApprovalMode, str] | None] = (
            ContextVar(f"task-dispatcher-permission-{id(self)}", default=None)
        )
        mutations = dependencies.get("mutations")
        if mutations is None:
            mutations = WorkspaceMutationPool(source_services, self.capture)
        if not isinstance(mutations, WorkspaceMutationPool):
            raise TypeError("mutations must be a WorkspaceMutationPool")
        self._mutations = mutations
        self.subagents = None
        self.interactive = False

    @property
    def editor(self) -> WorkspaceEditor:
        return self._mutations.for_services(self._source).editor

    @property
    def verification_allow_sensitive_paths(self) -> bool:
        """Freeze the Host path capability for task verification composition."""
        return self._source.guard.allow_sensitive

    def tools(self) -> Sequence[ToolDefinition]:
        return self._dispatcher(self._source).tools()

    def resolve_supervision_action(self, request: ActionRequest) -> ActionRequest:
        """Resolve active plugin identity without constructing workspace services."""
        return resolve_plugin_action(request, self.plugins)

    @property
    def workspace_fingerprint(self) -> str:
        return self._mutations.for_services(self._source).workspace_fingerprint

    @contextmanager
    def permission_scope(
        self, mode: ApprovalMode, source: str = "session"
    ) -> Iterator[None]:
        if not isinstance(mode, ApprovalMode):
            raise TypeError("mode must be an ApprovalMode")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("permission source must be non-blank text")
        token = self._permission_override.set((mode, source.strip()))
        try:
            yield
        finally:
            self._permission_override.reset(token)

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: object,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: object | None = None,
        source_check: object | None = None,
    ) -> ActionResult:
        services = self._runtime.services_for_root(
            self._authorized_root(task_authorization)
        )
        return await self._dispatcher(services).dispatch(
            request,
            cancellation,
            task_authorization,
            execution_context=execution_context,
            source_check=source_check,
        )

    def _authorized_root(self, authorization: TaskAuthorization | None) -> Path:
        if authorization is None:
            return self._source.root
        return Path(authorization.workspace_root).resolve()

    def _dispatcher(self, service: WorkspaceServices) -> RootActionDispatcher:
        mutations = self._mutations.for_services(service)
        capture = (
            self.capture
            if service.root.resolve() == self._source.root.resolve()
            else mutations.capture
        )
        dispatcher = RootActionDispatcher(
            service.files,
            mutations.editor,
            self._policy_for(service.root),
            self.approvals,
            git=service.git,
            runtime=service.runtime,
            verification=service.verification,
            web_access=self.web_access,
            enable_structured_verification=structured_verification_enabled(),
            mcp=self.mcp,
            plugins=self.plugins,
            subagents=self.subagents,
            threads=self.threads,
            peers=self.peers,
            capture=capture,
            caller_thread=self.caller_thread,
            invalidate_cache=lambda paths: _invalidate(service, paths),
            edit_plans=mutations.edit_plans,
            workspace_fingerprint=mutations.workspace_fingerprint,
            repo_index=service.repo_index,
            process_rules=self.process_rules,
            permission_workspace_root=self._source.root,
            permission_workspace_fingerprint=self.workspace_fingerprint,
            permission_source=(
                self._permission_override.get()[1]
                if self._permission_override.get() is not None
                else None
            ),
            metrics=self.metrics,
        )
        dispatcher.interactive = self.interactive
        return dispatcher

    def _policy_for(self, root: Path) -> ActionPolicy:
        override = self._permission_override.get()
        return ActionPolicy(
            replace(
                self.policy.config,
                approval_mode=(
                    override[0] if override is not None else self.policy.config.approval_mode
                ),
                workspace_root=root,
                mcp_risks=dict(self.policy.config.mcp_risks),
            )
        )


def _invalidate(service: WorkspaceServices, paths: Sequence[str]) -> None:
    service.repo_index.invalidate(paths)
    service.files.invalidate_inventory()


__all__ = ["TaskScopedDispatcher"]
