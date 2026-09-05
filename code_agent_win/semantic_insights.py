from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from code_agent.semantic_insights import InsightKind, SemanticInsightReport, SemanticInsightService


class SemanticGraphControl:
    """Read semantic insights from the active thread's shared repository index."""

    def __init__(
        self,
        source_root: Path,
        workspace_runtime: object,
        service: SemanticInsightService | None = None,
    ) -> None:
        if not isinstance(source_root, Path):
            raise TypeError("source_root must be a Path")
        self._source_root = source_root.resolve()
        self._workspace_runtime = workspace_runtime
        self._service = service or SemanticInsightService()

    async def analyze(
        self,
        kind: str,
        arguments: Sequence[str] = (),
        *,
        thread_id: str | None = None,
        limit: int = 12,
        offset: int = 0,
    ) -> SemanticInsightReport:
        resolved = InsightKind(kind)
        root = await self._active_root(thread_id)
        services = self._workspace_runtime.services_for_root(root)
        index = getattr(services, "repo_index", None)
        return await asyncio.to_thread(self._analyze, index, root, resolved, arguments, limit, offset)

    def _analyze(
        self, index: object, root: Path, kind: InsightKind,
        arguments: Sequence[str], limit: int, offset: int
    ) -> SemanticInsightReport:
        snapshot_for_turn = getattr(index, "snapshot_for_turn", None)
        invalidate = getattr(index, "invalidate", None)
        if not callable(snapshot_for_turn) or not callable(invalidate):
            raise RuntimeError("semantic repository index is unavailable")
        invalidate(())
        lexical_paths: tuple[str, ...] = ()
        if kind in {InsightKind.CONTEXT, InsightKind.LOCATE}:
            query_for_turn = getattr(index, "query_for_turn", None)
            if callable(query_for_turn):
                query = " ".join(arguments)
                snapshot, ranks = query_for_turn(query)
                lexical_paths = tuple(getattr(ranks, "paths", ()))
            else:
                snapshot = snapshot_for_turn()
        else:
            snapshot = snapshot_for_turn()
        report = self._service.analyze(
            snapshot, kind, arguments, lexical_paths=lexical_paths, limit=limit, offset=offset
        )
        coverage = f"Workspace: {root} · point-in-time snapshot · index file cap {getattr(index, 'max_files', 'unknown')}"
        return replace(report, warnings=(coverage, *report.warnings))

    async def _active_root(self, thread_id: str | None) -> Path:
        if not thread_id:
            return self._source_root
        root_for_thread = getattr(self._workspace_runtime, "root_for_thread", None)
        if not callable(root_for_thread):
            return self._source_root
        root = root_for_thread(thread_id)
        if root is None:
            hydrate = getattr(self._workspace_runtime, "hydrate_bindings", None)
            if callable(hydrate):
                await hydrate()
                root = root_for_thread(thread_id)
        return self._source_root if root is None else Path(root).resolve()
