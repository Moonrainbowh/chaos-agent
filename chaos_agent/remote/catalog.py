from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_agent.sessions.errors import SessionNotFound
from code_agent.project_launcher.store import ProjectStore

from .errors import RemoteConflict, RemoteNotFound


SESSION_PROJECT_LABEL = "remote-session"
UNASSIGNED = "unassigned"


def canonical_root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def project_id(root: Path) -> str:
    identity = os.path.normcase(str(canonical_root(root)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _text(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


@dataclass
class CatalogSnapshot:
    projects: dict[str, dict[str, Any]]
    sessions: list[dict[str, Any]]
    tasks: dict[str, Any]
    current_project_id: str | None

    def session(self, identifier: str) -> dict[str, Any]:
        for item in self.sessions:
            if item["id"] == identifier:
                return item
        raise RemoteNotFound("unknown session")

    def project(self, identifier: str) -> dict[str, Any]:
        try:
            return self.projects[identifier]
        except KeyError:
            raise RemoteNotFound("unknown project") from None


class RemoteCatalog:
    """Project public session records without constructing a provider runtime."""

    def __init__(self, application: object, project_store: ProjectStore | None = None) -> None:
        self.project_store = project_store
        self._registry_lock = asyncio.Lock()
        self.sessions = getattr(application, "sessions", None)
        self.workspace_runtime = getattr(application, "workspace_runtime", None)
        value = getattr(application, "workspace_root", None)
        self.current_root = canonical_root(value) if value is not None else None

    async def snapshot(self) -> CatalogSnapshot:
        roots: dict[str, Path] = {}
        current_id = None
        if self.current_root is not None:
            current_id = project_id(self.current_root)
            roots[current_id] = self.current_root
        tasks = {}
        if self.sessions is not None:
            tasks = {task.thread_id: task for task in await self.sessions.list_tasks(include_terminal=True)}
        rows: list[dict[str, Any]] = []
        offset = 0
        while self.sessions is not None:
            batch = await self.sessions.list_threads(limit=200, offset=offset, include_archived=True)
            for start in range(0, len(batch), 16):
                projected = await asyncio.gather(
                    *(self._row(thread, tasks.get(thread.id)) for thread in batch[start:start + 16]),
                    return_exceptions=True,
                )
                for value in projected:
                    if isinstance(value, BaseException):
                        raise value
                    row, root = value
                    if root is not None:
                        roots[row["project_id"]] = root
                    rows.append(row)
            if len(batch) < 200:
                break
            offset += len(batch)
        registered = {}
        recent = None
        if self.project_store is not None:
            def registry_snapshot():
                self.project_store.seed(roots.values())
                return self.project_store.entries(), self.project_store.last_root()
            entries, recent = await self.project_operation(registry_snapshot)
            registered = {entry.identifier: entry for entry in entries}
            # The current Host root has no historical identity until a session exists.
            if current_id not in registered and not any(row["project_id"] == current_id for row in rows):
                roots.pop(current_id, None)
                current_id = None
            roots.update({entry.identifier: entry.root for entry in entries})
        projects = {}
        for identifier, root in roots.items():
            members = [row for row in rows if row["project_id"] == identifier]
            projects[identifier] = {
                "id": identifier, "name": root.name or str(root), "path": str(root),
                "session_count": len(members),
                "updated_at": max((row["updated_at"] for row in members), default=None),
                "available": await asyncio.to_thread(root.is_dir),
                "registered": identifier in registered,
                "recent": recent is not None and project_id(recent) == identifier,
            }
        unassigned = [row for row in rows if row["project_id"] == UNASSIGNED]
        if unassigned:
            projects[UNASSIGNED] = {
                "id": UNASSIGNED, "name": "未归类", "path": "",
                "session_count": len(unassigned),
                "updated_at": max(row["updated_at"] for row in unassigned),
                "available": False,
                "registered": False, "recent": False,
            }
        return CatalogSnapshot(projects, rows, tasks, current_id)

    async def project_operation(self, operation, *args):
        """Serialize this Host's registry operations before scheduling blocking IO."""
        async with self._registry_lock:
            return await asyncio.to_thread(operation, *args)

    async def _row(self, thread: Any, task: Any) -> tuple[dict[str, Any], Path | None]:
        root = await self._source_root(thread.id, task)
        identifier = project_id(root) if root is not None else UNASSIGNED
        preview = await self._preview(thread.id) if thread.message_count else ""
        fallback = getattr(getattr(task, "contract", None), "objective", None)
        title = _text(thread.title or fallback or preview, 96) or "新会话"
        status = getattr(task, "status", thread.status)
        if getattr(thread.status, "value", thread.status) == "archived":
            status = thread.status
        return {
            "id": thread.id, "title": title, "preview": preview,
            "project_id": identifier, "project_name": root.name if root is not None else "未归类",
            "updated_at": thread.updated_at.isoformat(),
            "status": getattr(status, "value", status), "message_count": thread.message_count,
        }, root

    async def _source_root(self, thread_id: str, task: Any) -> Path | None:
        if task is not None:
            try:
                lineage = await self.sessions.load_lineage_for_task(task.id)
                return canonical_root(lineage.source_root)
            except SessionNotFound:
                return canonical_root(task.contract.authorization.workspace_root)
        for checkpoint in reversed(await self.sessions.list_checkpoints(thread_id)):
            root = checkpoint.metadata.get("source_root")
            if checkpoint.label == SESSION_PROJECT_LABEL and isinstance(root, str) and Path(root).is_absolute():
                return canonical_root(root)
        relation = await self.sessions.load_thread_relation(thread_id)
        if relation.parent_thread_id is not None:
            parent = relation.parent_thread_id
            return await self._source_root(parent, await self.sessions.load_task_for_thread(parent))
        return None

    async def _preview(self, thread_id: str) -> str:
        before = None
        while True:
            records = await self.sessions.load_message_records(thread_id, before_sequence=before, limit=20)
            for record in reversed(records):
                if record.message.role in {"user", "assistant"} and record.message.content.strip():
                    return _text(record.message.content, 160)
            if len(records) < 20:
                return ""
            before = records[0].sequence

    async def list_sessions(self, *, selected_project: str | None, query: str, offset: int, limit: int) -> dict[str, Any]:
        snapshot = await self.snapshot()
        if selected_project:
            snapshot.project(selected_project)
        matches = []
        folded = query.casefold()
        for row in snapshot.sessions:
            if selected_project and row["project_id"] != selected_project:
                continue
            if folded and not await self._matches(row, folded):
                continue
            matches.append(row)
        page = matches[offset:offset + limit]
        next_offset = offset + len(page) if offset + len(page) < len(matches) else None
        return {"sessions": page, "next_offset": next_offset}

    async def _matches(self, row: dict[str, Any], query: str) -> bool:
        if any(query in row[field].casefold() for field in ("title", "id", "preview")):
            return True
        before = None
        while True:
            records = await self.sessions.load_message_records(row["id"], before_sequence=before, limit=100)
            if any(record.message.role in {"user", "assistant"} and query in record.message.content.casefold() for record in records):
                return True
            if len(records) < 100:
                return False
            before = records[0].sequence

    async def create_session(self, identifier: str) -> dict[str, str]:
        snapshot = await self.snapshot()
        project = snapshot.project(identifier)
        self.require_available(project)
        if self.sessions is None:
            raise RemoteConflict("session storage is unavailable")
        thread_id = await self.sessions.create_thread()
        if self.workspace_runtime is not None:
            self.workspace_runtime.bind_thread(thread_id, Path(project["path"]))
        await self.sessions.create_checkpoint(thread_id, SESSION_PROJECT_LABEL, {"source_root": project["path"]})
        return {"session_id": thread_id, "project_id": identifier}

    @staticmethod
    def require_available(project: dict[str, Any]) -> None:
        if not project["available"]:
            raise RemoteConflict("project directory is unavailable; choose an available project")

    async def message_page(self, thread_id: str, *, before: int | None, limit: int, maximum: int | None = None) -> tuple[list[dict[str, Any]], int | None]:
        cursor = min(before, maximum + 1) if before is not None and maximum is not None else before
        if cursor is None and maximum is not None:
            cursor = maximum + 1
        public = []
        while len(public) <= limit:
            records = await self.sessions.load_message_records(thread_id, before_sequence=cursor, limit=limit + 1)
            public = [
                {"sequence": record.sequence, "role": record.message.role, "content": record.message.content}
                for record in records if record.message.role in {"user", "assistant"}
            ] + public
            if len(records) < limit + 1 or not records:
                break
            cursor = records[0].sequence
        has_older = len(public) > limit
        page = public[-limit:]
        return page, page[0]["sequence"] if has_older and page else None
