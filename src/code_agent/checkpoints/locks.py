from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class LineageLockPool:
    """Serialize preview, capture, execute, and recovery within one process."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def for_lineage(self, lineage_id: str):
        async with self._guard:
            lock = self._locks.setdefault(lineage_id, asyncio.Lock())
        async with lock:
            yield
