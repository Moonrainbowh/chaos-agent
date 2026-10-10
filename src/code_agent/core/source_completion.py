"""Typed, deterministic full-source completion contract, never semantic proof."""
from dataclasses import dataclass
from typing import Protocol

from .models import Message


def freeze_sources(paths) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes)):
        raise TypeError("required_sources must be a sequence")
    values = tuple(paths)
    if len(values) > 32 or any(not isinstance(p, str) or not p.strip() or len(p) > 1024 for p in values):
        raise ValueError("required_sources must contain at most 32 bounded paths")
    if len(set(values)) != len(values):
        raise ValueError("required_sources must be unique")
    return values


@dataclass(frozen=True)
class SourceCompletionSnapshot:
    required: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()
    correction_baseline: tuple[str, ...] | None = None
    correction_id: str | None = None
    budget_exhausted: str | None = None

    def __post_init__(self):
        if self.budget_exhausted is not None and (not isinstance(self.budget_exhausted, str) or len(self.budget_exhausted) > 256):
            raise ValueError('invalid source budget fact')
        for name in ("required", "completed", "correction_baseline"):
            values = getattr(self, name)
            if values is not None:
                object.__setattr__(self, name, freeze_sources(values))
        if not set(self.completed).issubset(self.required):
            raise ValueError("source facts outside frozen requirements")
        if self.correction_baseline is not None and not set(self.correction_baseline).issubset(self.required):
            raise ValueError("invalid source correction baseline")

    @property
    def remaining(self):
        return tuple(path for path in self.required if path not in self.completed)

    @property
    def stalled(self):
        return self.correction_baseline is not None and not set(self.completed).difference(self.correction_baseline)


class SourceCompletionHost(Protocol):
    async def snapshot(self, thread_id: str, supplied: tuple[str, ...] | None) -> SourceCompletionSnapshot: ...
    async def correct(self, thread_id: str, snapshot: SourceCompletionSnapshot) -> tuple[Message, ...]: ...
