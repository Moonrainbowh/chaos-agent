"""Read project origins through existing session APIs, without provider creation."""
from __future__ import annotations

import os
from pathlib import Path

from code_agent.sessions.errors import SessionNotFound


def same_root(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


class MobileCatalog:
    def __init__(self, sessions):
        self.sessions = sessions

    async def source_root(self, thread_id, seen=None):
        seen = set() if seen is None else seen
        if thread_id in seen or len(seen) >= 32:
            return None
        seen.add(thread_id)
        task = await self.sessions.load_task_for_thread(thread_id)
        if task is not None:
            try:
                lineage = await self.sessions.load_lineage_for_task(task.id)
                return Path(lineage.source_root).resolve()
            except SessionNotFound:
                return Path(task.contract.authorization.workspace_root).resolve()
        for checkpoint in reversed(await self.sessions.list_checkpoints(thread_id)):
            root = checkpoint.metadata.get("source_root")
            if checkpoint.label == "remote-session" and isinstance(root, str) and Path(root).is_absolute():
                return Path(root).resolve()
        relation = await self.sessions.load_thread_relation(thread_id)
        if relation.parent_thread_id:
            return await self.source_root(relation.parent_thread_id, seen)
        return None

    async def project_roots(self):
        roots = []
        for task in await self.sessions.list_tasks(include_terminal=True):
            root = await self.source_root(task.thread_id)
            if root is not None and not any(same_root(root, known) for known in roots):
                roots.append(root)
                if len(roots) >= 64:
                    break
        return tuple(roots)

    async def threads(self, root, *, limit=100, offset=0, include_archived=False):
        matches, scanned = [], 0
        while True:
            batch = await self.sessions.list_threads(limit=200, offset=scanned, include_archived=include_archived)
            for thread in batch:
                source = await self.source_root(thread.id)
                if source is not None and same_root(source, root):
                    matches.append(thread)
                    if len(matches) >= offset + limit:
                        return tuple(matches[offset:])
            if len(batch) < 200:
                return tuple(matches[offset:offset + limit])
            scanned += len(batch)


class ProjectSessionView:
    """Scope the mobile history menu and reads to the selected project root."""
    def __init__(self, catalog, root):
        self.catalog, self.root = catalog, root

    async def list_threads(self, **kwargs):
        return await self.catalog.threads(self.root, **kwargs)

    async def _read(self, name, thread_id, *args, **kwargs):
        source = await self.catalog.source_root(thread_id)
        if source is None or not same_root(source, self.root):
            raise ValueError("会话不属于当前项目，请先切换项目")
        return await getattr(self.catalog.sessions, name)(thread_id, *args, **kwargs)

    async def load_messages(self, thread_id):
        return await self._read("load_messages", thread_id)

    async def load_events(self, thread_id):
        return await self._read("load_events", thread_id)

    async def list_goals(self, thread_id):
        return await self._read("list_goals", thread_id)

    async def list_checkpoints(self, thread_id):
        return await self._read("list_checkpoints", thread_id)

    def __getattr__(self, name):
        return getattr(self.catalog.sessions, name)
