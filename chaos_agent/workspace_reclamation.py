"""Reclaim managed worktrees that provably hold no task work.

Nothing used to remove a managed worktree after its task ended, so
``<storage>/worktrees/<repository>/<lineage>`` grew by one full checkout per
isolated task. Reclamation is deliberately narrow, because a worktree holds
the user's work until it is proven otherwise.

A worktree is reclaimed only when every one of these holds:

* its lineage is not live in this process and has no pending rewind;
* either no lineage was ever persisted for it — creation crashed between
  ``git worktree add`` and the lineage write — or its owning task reached a
  terminal state;
* the lineage has no checkpoint snapshot, unless the caller says otherwise;
* the worktree is a registered Git worktree on the branch this tool creates,
  that branch still points at the recorded commit, and the checkout is clean.

The last two points are what makes removal safe: there is no captured code to
rewind to, nothing was committed, and nothing is uncommitted, so the directory
reproduces exactly the commit it was created from. A worktree that holds
commits, uncommitted edits, or rewindable snapshots is reported and left alone
— its branch and its checkpoints keep the work, and only the user can decide
to drop it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from code_agent.sessions.errors import SessionNotFound
from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands
from code_agent.workspace._worktree_lock import valid_repository_id
from code_agent.workspace._worktree_models import ManagedWorktree
from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.worktrees import WorktreeManager

from code_agent_win.workspace_models import task_branch_name


@dataclass(frozen=True)
class ReclaimedWorktree:
    root: Path
    reason: str


@dataclass(frozen=True)
class RetainedWorktree:
    root: Path
    reason: str


@dataclass(frozen=True)
class ReclamationReport:
    reclaimed: tuple[ReclaimedWorktree, ...] = ()
    retained: tuple[RetainedWorktree, ...] = ()

    @property
    def reclaimed_roots(self) -> tuple[Path, ...]:
        return tuple(item.root for item in self.reclaimed)


@dataclass(frozen=True)
class _Candidate:
    repository_id: str
    lineage_id: str
    root: Path


@dataclass(frozen=True)
class _Eligibility:
    eligible: bool
    reason: str


async def reclaim_worktrees(
    worktrees: WorktreeManager,
    sessions: object,
    *,
    live_lineage_ids: Iterable[str] = (),
    rewindable: bool = False,
) -> ReclamationReport:
    """Remove every managed worktree that holds no task work.

    ``rewindable`` controls the snapshot gate. The default keeps any worktree
    whose lineage still has a checkpoint snapshot, because removing it would
    leave an executable rewind without a directory to restore into. An
    explicit caller that accepts that trade-off sets it to ``True``.

    Best-effort by design: a worktree that cannot be classified or removed is
    reported and kept, because losing a task's work is far worse than keeping
    a directory around for another run.
    """
    candidates = candidate_worktrees(worktrees.storage_root)
    if not candidates:
        return ReclamationReport()
    live = frozenset(str(value) for value in live_lineage_ids)
    eligibility = await _eligibility(sessions, candidates, live, rewindable)
    return await asyncio.to_thread(_retire, worktrees, candidates, eligibility)


def candidate_worktrees(storage_root: Path) -> tuple[_Candidate, ...]:
    """Return the ``<storage>/<repository>/<lineage>`` directories on disk."""
    found: list[_Candidate] = []
    for repository in _directories(storage_root):
        if not valid_repository_id(repository.name):
            continue
        for lineage in _directories(repository):
            lineage_id = _normalized_lineage_id(lineage.name)
            if lineage_id is not None:
                found.append(_Candidate(repository.name, lineage_id, lineage))
    return tuple(found)


def _directories(root: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(item for item in root.iterdir() if item.is_dir()))
    except OSError:
        return ()


def _normalized_lineage_id(name: str) -> str | None:
    try:
        return uuid.UUID(name).hex
    except (AttributeError, ValueError):
        return None


async def _eligibility(
    sessions: object,
    candidates: tuple[_Candidate, ...],
    live: frozenset[str],
    rewindable: bool,
) -> dict[str, _Eligibility]:
    pending = await _pending_lineage_ids(sessions)
    verdicts: dict[str, _Eligibility] = {}
    for candidate in candidates:
        lineage_id = candidate.lineage_id
        if lineage_id in live:
            verdicts[lineage_id] = _Eligibility(False, "the worktree is in use")
        elif pending is None:
            verdicts[lineage_id] = _Eligibility(
                False, "pending rewinds could not be read"
            )
        elif lineage_id in pending:
            verdicts[lineage_id] = _Eligibility(
                False, "a rewind is pending for this lineage"
            )
        else:
            verdicts[lineage_id] = await _lineage_eligibility(
                sessions, lineage_id, rewindable
            )
    return verdicts


async def _pending_lineage_ids(sessions: object) -> frozenset[str] | None:
    try:
        operations = await sessions.pending_rewinds()
    except Exception:
        return None
    return frozenset(str(item.lineage_id) for item in operations)


async def _lineage_eligibility(
    sessions: object, lineage_id: str, rewindable: bool
) -> _Eligibility:
    try:
        record = await sessions.load_lineage(lineage_id)
    except SessionNotFound:
        return _Eligibility(True, "no lineage was persisted for this worktree")
    except Exception:
        return _Eligibility(False, "the lineage could not be read")
    if not rewindable and await _has_snapshot(sessions, lineage_id):
        return _Eligibility(False, "the lineage still has rewindable checkpoints")
    if record.owner_task_id is None:
        return _Eligibility(False, "the lineage has no owning task")
    try:
        task = await sessions.load_task(record.owner_task_id)
    except Exception:
        return _Eligibility(False, "the owning task could not be read")
    if not task.status.is_terminal:
        return _Eligibility(False, f"the owning task is {task.status.value}")
    return _Eligibility(True, "the owning task reached a terminal state")


async def _has_snapshot(sessions: object, lineage_id: str) -> bool:
    try:
        return bool(await sessions.lineage_has_workspace_snapshots(lineage_id))
    except Exception:
        return True


def _retire(
    worktrees: WorktreeManager,
    candidates: tuple[_Candidate, ...],
    eligibility: dict[str, _Eligibility],
) -> ReclamationReport:
    reclaimed: list[ReclaimedWorktree] = []
    retained: list[RetainedWorktree] = []
    for candidate in candidates:
        verdict = eligibility.get(candidate.lineage_id)
        if verdict is None or not verdict.eligible:
            reason = "unclassified" if verdict is None else verdict.reason
            retained.append(RetainedWorktree(candidate.root, reason))
            continue
        hold = _retire_one(worktrees, candidate)
        if hold is None:
            reclaimed.append(ReclaimedWorktree(candidate.root, verdict.reason))
        else:
            retained.append(RetainedWorktree(candidate.root, hold))
    return ReclamationReport(tuple(reclaimed), tuple(retained))


def _retire_one(worktrees: WorktreeManager, candidate: _Candidate) -> str | None:
    """Remove one worktree, or return why it was kept."""
    source_root = _source_root(worktrees, candidate)
    if source_root is None:
        return "the worktree does not identify a source repository"
    branch = task_branch_name(candidate.lineage_id)
    commands = FixedGitWorktreeCommands(GitWorkspace(source_root))
    try:
        entry = commands.entry(candidate.root)
        if entry is None:
            return "the path is not a registered Git worktree"
        if not entry.head or entry.branch != f"refs/heads/{branch}":
            return "the worktree branch is not the one this tool creates"
        if not commands.is_ancestor(entry.head, commands.head_commit()):
            return "the task branch holds commits of its own"
        if commands.branch_tip(branch) != entry.head:
            return "the task branch no longer matches the worktree"
        record = ManagedWorktree(
            candidate.repository_id,
            candidate.lineage_id,
            source_root,
            candidate.root,
            branch,
            entry.head,
        )
        worktrees.remove(record, confirmed=True, active=False)
        commands.delete_branch_if_matches(branch, entry.head)
    except (OSError, ValueError, WorkspaceError) as error:
        return f"reclamation stopped: {error}"
    return None


def _source_root(worktrees: WorktreeManager, candidate: _Candidate) -> Path | None:
    try:
        commands = FixedGitWorktreeCommands(GitWorkspace(candidate.root))
        source_root = commands.common_dir().parent
        identity = worktrees.identify(source_root)
    except (OSError, ValueError, WorkspaceError):
        return None
    return source_root if identity.repository_id == candidate.repository_id else None


__all__ = [
    "ReclaimedWorktree",
    "ReclamationReport",
    "RetainedWorktree",
    "candidate_worktrees",
    "reclaim_worktrees",
]
