"""Project references enter the normal fixed-prefix budget before model requests."""
import json
from dataclasses import replace

from code_agent.context.tokens import estimate_tokens
from code_agent.core.context_request import ContextRequest


class ProjectMemoryContextBuilder:
    def __init__(self, inner, control):
        self.inner, self.control = inner, control

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def build(self, request):
        if not isinstance(request, ContextRequest):
            raise TypeError("project memory requires typed ContextRequest")
        request.cancellation.raise_if_cancelled()
        try:
            await self.control.project_id_for(request.thread_id)
        except PermissionError:
            # Optional references cannot veto an independently authorized task.
            return await self.inner.build(replace(request, project_memory=""))
        query = request.user_input
        if not query.strip():
            query = next((message.content for message in reversed(request.messages)
                          if message.role == "user"), "")
            if not query.strip():
                records = await self.control.sessions.read_history_page(request.thread_id,
                    role="user", newest=True, limit=1, max_bytes=1024 * 1024)
                query = records[0].message.content if records else ""
        selected = []
        if query.strip():
            try:
                records = await self.control.search(query[:512], thread_id=request.thread_id)
            except PermissionError:
                return await self.inner.build(replace(request, project_memory=""))
            for record in records:
                item = dict(id=record.memory_id, revision=record.revision, kind=record.kind,
                    source_refs=dict(record.source_refs), conditions=dict(record.conditions),
                    content=record.content, applicability="applicable", origin=record.origin)
                candidate = json.dumps(selected + [item], ensure_ascii=False, separators=(",", ":"))
                if estimate_tokens(candidate) <= 1024 and len(candidate.encode("utf-8")) <= 16384:
                    selected.append(item)
        payload = json.dumps(selected, ensure_ascii=False, separators=(",", ":")) if selected else ""
        request.cancellation.raise_if_cancelled()
        return await self.inner.build(replace(request, project_memory=payload))
