from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ._worktree_models import ManagedWorktree
from .errors import WorkspaceError


@dataclass(frozen=True)
class UnclaimedWorktreeLease:
    """Opaque, manager-issued authority over one not-yet-persisted worktree."""

    worktree: ManagedWorktree
    _token: object = field(repr=False, compare=False)


class WorktreeLeaseRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[object, UnclaimedWorktreeLease] = {}

    def issue(self, worktree: ManagedWorktree) -> UnclaimedWorktreeLease:
        token = object()
        lease = UnclaimedWorktreeLease(worktree, token)
        with self._lock:
            self._leases[token] = lease
        return lease

    def claim(self, lease: UnclaimedWorktreeLease) -> ManagedWorktree:
        with self._lock:
            worktree = self._require(lease)
            del self._leases[lease._token]
            return worktree

    def discard(
        self,
        lease: UnclaimedWorktreeLease,
        action: Callable[[ManagedWorktree], None],
    ) -> None:
        with self._lock:
            worktree = self._require(lease)
            action(worktree)
            del self._leases[lease._token]

    def _require(self, lease: UnclaimedWorktreeLease) -> ManagedWorktree:
        if type(lease) is not UnclaimedWorktreeLease:
            raise WorkspaceError("unclaimed worktree cleanup requires its exact lease")
        if self._leases.get(lease._token) is not lease:
            raise WorkspaceError("unclaimed worktree lease is unknown or already consumed")
        return lease.worktree
