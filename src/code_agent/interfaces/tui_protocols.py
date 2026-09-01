from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class SessionBrowser(Protocol):
    async def list_threads(self, *, limit: int = 100) -> Sequence[object]: ...


class EvidenceReader(Protocol):
    async def list_verification_evidence(
        self, task_id: str
    ) -> Sequence[object]: ...
