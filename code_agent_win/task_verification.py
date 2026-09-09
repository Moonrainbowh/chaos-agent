from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.core.completion_contract import TaskIntent


class TaskScopedVerificationService:
    """Keep one verification transaction per durable task and workspace root."""

    def __init__(
        self,
        sessions: object,
        semantic_snapshot: Callable[[Path], object] | None = None,
        initial_service: tuple[Path, LedgerTaskVerificationService] | None = None,
    ) -> None:
        if semantic_snapshot is not None and not callable(semantic_snapshot):
            raise TypeError("semantic_snapshot must be callable or None")
        self._sessions = sessions
        self._semantic_snapshot = semantic_snapshot
        self._services: dict[str, tuple[Path, LedgerTaskVerificationService]] = {}
        self._initial_service = initial_service

    async def prepare(self, task: object, state: object) -> object:
        return await self._service(task).prepare(task, state)

    def begin_logical_change(self, task_id: str) -> None:
        service = self._existing(task_id)
        service.begin_logical_change(task_id)

    async def commit_logical_change(
        self, task: object, state: object
    ) -> tuple[object, object | None]:
        service = self._service(task)
        await self._refresh(service, self._root(task))
        settled, plan = await service.commit_logical_change(task, state)
        return settled, service.milestone_verification(task, plan)

    def rollback_logical_change(self, task_id: str) -> None:
        self._existing(task_id).rollback_logical_change(task_id)

    async def record_action(
        self, task: object, request: object, result: object, state: object
    ) -> object:
        service = self._service(task)
        if (
            getattr(request, "name", None)
            in {"run_command", "run_process_v1", "run_verification"}
            and service.in_logical_change(task.id)
        ):
            await self._refresh(service, self._root(task))
        return await service.record_action(task, request, result, state)

    async def assess(self, task: object, state: object) -> object:
        return await self._service(task).assess(task, state)

    async def suggest_verification(self, task: object, state: object) -> object:
        service = self._service(task)
        if state.files_changed:
            await self._refresh(service, self._root(task))
        return await service.suggest_verification(task, state)

    async def finalize(self, task: object, assessment: object) -> object:
        return await self._service(task).finalize(task, assessment)

    async def _refresh(
        self, service: LedgerTaskVerificationService, root: Path
    ) -> None:
        if self._semantic_snapshot is None:
            return
        snapshot = await asyncio.to_thread(self._semantic_snapshot, root)
        service.set_semantic_snapshot(snapshot)

    def _service(self, task: object) -> LedgerTaskVerificationService:
        task_id = getattr(task, "id", None)
        if not isinstance(task_id, str) or not task_id:
            raise TypeError("task must provide a non-blank id")
        root = self._root(task)
        existing = self._services.get(task_id)
        if existing is not None:
            if existing[0] != root:
                raise RuntimeError("task workspace root changed")
            return existing[1]
        initial = self._initial_service
        if initial is not None and initial[0].resolve() == root:
            service = initial[1]
            self._initial_service = None
        else:
            service = LedgerTaskVerificationService(root, self._sessions)
        self._services[task_id] = (root, service)
        return service

    def _existing(self, task_id: str) -> LedgerTaskVerificationService:
        existing = self._services.get(task_id)
        if existing is None:
            raise RuntimeError("task verification was not prepared")
        return existing[1]

    @staticmethod
    def _root(task: object) -> Path:
        value = task.contract.authorization.workspace_root
        return Path(value).resolve()


__all__ = ["TaskScopedVerificationService"]
