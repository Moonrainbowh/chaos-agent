"""User-controlled memory bound to a Host-owned original project capability."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.models import MemoryLifecycle
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands


def project_identity(root: Path) -> str:
    """Use actual Git common-dir identity, or a canonical independent workspace."""
    root = Path(root).resolve()
    git = GitWorkspace(root)
    identity = ("git:" + os.path.normcase(str(FixedGitWorktreeCommands(git).common_dir()))
                if git.is_repository() else "workspace:" + os.path.normcase(str(root)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _bounded_text(value, label, max_bytes):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be non-blank text")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(label + " exceeds its byte limit")
    return value


def _metadata(value, label):
    if value is None:
        return None
    from code_agent.core._json import validate_json_mapping
    validate_json_mapping(value, label)
    if len(json.dumps(dict(value), ensure_ascii=False).encode("utf-8")) > 4096:
        raise ValueError(label + " exceeds its byte limit")
    return dict(value)


class ProjectMemoryControl:
    """Every operation fixes scope through current Host identity, never caller text."""

    def __init__(self, sessions, source_root: Path):
        self.sessions = sessions
        self.source_root = Path(source_root).resolve()
        self._identity = None
        self._bound_threads = set()

    def bind_thread(self, thread_id):
        """Host-only registration immediately after trusted local thread creation."""
        self._bound_threads.add(_bounded_text(thread_id, "thread_id", 256))

    async def project_id_for(self, thread_id=None):
        if self._identity is None:
            self._identity = await asyncio.to_thread(project_identity, self.source_root)
        if thread_id is not None:
            _bounded_text(thread_id, "thread_id", 256)
            await self._check_thread(thread_id, set())
        return self._identity

    async def _check_thread(self, thread_id, seen):
        if thread_id in seen or len(seen) >= 32:
            raise PermissionError("project memory thread ancestry is invalid")
        seen.add(thread_id)
        # Assert existence before any fallback to the Host root.
        relation = await self.sessions.load_thread_relation(thread_id)
        task = await self.sessions.load_task_for_thread(thread_id)
        if task is not None:
            try:
                lineage = await self.sessions.load_lineage_for_task(task.id)
                root = Path(lineage.source_root)
            except SessionNotFound:
                root = Path(task.contract.authorization.workspace_root)
            if await asyncio.to_thread(project_identity, root) != self._identity:
                raise PermissionError("thread belongs to another project")
        if relation.parent_thread_id:
            await self._check_thread(relation.parent_thread_id, seen)
        elif task is None:
            if thread_id not in self._bound_threads:
                raise PermissionError("taskless thread lacks a trusted Host project binding")

    async def save(self, content, *, thread_id=None, kind="decision", conditions=None, source_refs=None):
        scope = await self.project_id_for(thread_id)
        refs = _metadata(source_refs, "source_refs") or {}
        refs.update(source_root=str(self.source_root), entry_origin="user_explicit",
                    explicit_user_thread=thread_id)
        return await self.sessions.create_memory("project", scope,
            _bounded_text(kind, "kind", 128), _bounded_text(content, "content", 8192),
            source_refs=_metadata(refs, "source_refs"), origin="user_explicit",
            conditions=_metadata(conditions, "conditions"), lifecycle=MemoryLifecycle.ACTIVE)

    async def list(self, *, thread_id=None, include_history=False, limit=50, offset=0):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.list_memories("project", scope,
            include_history=include_history, limit=limit, offset=offset, max_bytes=65536)

    async def show(self, memory_id, *, thread_id=None, revision=None):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.get_memory(memory_id, scope_type="project",
            scope_id=scope, revision=revision, max_bytes=16384)

    async def applicability(self, memory_id, *, thread_id=None):
        """Explain present applicability without treating active as verified truth."""
        record = await self.show(memory_id, thread_id=thread_id)
        if record.lifecycle is not MemoryLifecycle.ACTIVE:
            return record.lifecycle.value
        if not record.conditions:
            return "applicable"
        from code_agent.sessions._memory import assess_memory_applicability
        scope = await self.project_id_for(thread_id)
        return assess_memory_applicability(record,
            {"project_id": scope, "source_root": str(self.source_root)})

    async def revise(self, memory_id, content, *, expected_revision, thread_id=None,
                     conditions=None, source_refs=None):
        scope = await self.project_id_for(thread_id)
        refs = None
        if source_refs is not None:
            current = await self.show(memory_id, thread_id=thread_id)
            refs = _metadata(source_refs, "source_refs") or {}
            refs.update(source_root=str(self.source_root), entry_origin="user_explicit",
                        explicit_user_thread=current.source_refs.get("explicit_user_thread"))
        return await self.sessions.revise_memory(memory_id,
            _bounded_text(content, "content", 8192), expected_revision=expected_revision,
            scope_type="project", scope_id=scope,
            source_refs=_metadata(refs, "source_refs"),
            conditions=_metadata(conditions, "conditions"))

    async def withdraw(self, memory_id, *, revision, thread_id=None):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.set_memory_lifecycle(memory_id, revision=revision,
            lifecycle=MemoryLifecycle.WITHDRAWN, scope_type="project", scope_id=scope)

    async def delete(self, memory_id, *, thread_id=None):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.delete_memory(memory_id, scope_type="project", scope_id=scope)

    async def search(self, query, *, thread_id=None, limit=4, offset=0, require_applicable=True):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.search_memories(scope,
            _bounded_text(query, "query", 2048), limit=limit, offset=offset, max_bytes=16384,
            context={"project_id": scope, "source_root": str(self.source_root)},
            require_applicable=require_applicable)

    async def diagnostics(self, query, *, thread_id=None, limit=4, offset=0):
        scope = await self.project_id_for(thread_id)
        return await self.sessions.search_memory_diagnostics(scope,
            _bounded_text(query, "query", 2048), limit=limit, offset=offset, max_bytes=16384,
            context={"project_id": scope, "source_root": str(self.source_root)}, require_applicable=True)
