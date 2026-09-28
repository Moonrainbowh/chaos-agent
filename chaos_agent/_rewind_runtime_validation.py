from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from typing import Mapping

from code_agent.interfaces.rewind_models import RewindDisabledReason, RewindPath
from code_agent.sessions.rewind_models import (
    RewindMutationRecord, RewindMutationStatus, RewindObservation,
)
from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot
from code_agent.workspace.errors import (
    FileTooLargeError, SnapshotIntegrityError, SnapshotMissingError,
)
from code_agent.workspace.rewind_state import (
    WorkspaceFileState, observe_file_states, relevant_path_digest,
)
from code_agent.workspace.snapshot_store import SnapshotHandle


@dataclass(frozen=True)
class CodeProjection:
    paths: tuple[RewindPath, ...]
    states: tuple[WorkspaceFileState, ...]
    digest: str | None
    reason: RewindDisabledReason | None


def code_precheck(
    observation: RewindObservation, fingerprint: str
) -> RewindDisabledReason | None:
    fact = observation.checkpoint_fact
    if fact is None or fact.workspace_fingerprint != fingerprint:
        return RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE
    states = (fact.coverage_state, observation.heads.coverage_state)
    if any(state is not None and state.value == "invalidated" for state in states):
        return RewindDisabledReason.CODE_JOURNAL_INCOMPLETE
    if any(item.status is RewindMutationStatus.GAP for item in observation.mutations):
        return RewindDisabledReason.CODE_JOURNAL_INCOMPLETE
    if any(item.status is RewindMutationStatus.PREPARED for item in observation.mutations):
        return RewindDisabledReason.PENDING_WORKSPACE_MUTATION
    if observation.limit_exceeded:
        return RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
    return None


async def project_code(
    observation: RewindObservation, snapshots: object, editor: object
) -> CodeProjection:
    reason = code_precheck(observation, snapshots.workspace_fingerprint)
    if reason is not None:
        return CodeProjection((), (), None, reason)
    fact = observation.checkpoint_fact
    assert fact is not None
    owned = tuple(sorted((
        item for item in observation.mutations
        if item.owner_thread_id == fact.owner_thread_id
        and item.status is RewindMutationStatus.COMPLETED
    ), key=lambda item: item.sequence))
    if not owned:
        states: tuple[WorkspaceFileState, ...] = ()
        return CodeProjection((), states, relevant_path_digest(states), None)
    try:
        baselines = await _validate_snapshots(owned, snapshots)
        _validate_foreign_overlap(observation.mutations, owned)
        _validate_continuity(owned)
        paths = tuple(item[0] for item in sorted(
            baselines.values(), key=lambda item: (os.path.normcase(item[0]), item[0])
        ))
        states = await asyncio.to_thread(observe_file_states, editor, paths)
        _validate_current_tip(states, owned)
    except SnapshotMissingError:
        return CodeProjection((), (), None, RewindDisabledReason.SNAPSHOT_MISSING)
    except (SnapshotIntegrityError, TypeError, ValueError):
        return CodeProjection((), (), None, RewindDisabledReason.SNAPSHOT_INVALID)
    except FileTooLargeError:
        return CodeProjection(
            (), (), None, RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
        )
    except _WorkspaceConflict:
        return CodeProjection((), (), None, RewindDisabledReason.WORKSPACE_CONFLICT)
    projected = tuple(
        RewindPath(path, baselines[os.path.normcase(path)][1], True)
        for path in paths
    )
    return CodeProjection(projected, states, relevant_path_digest(states), None)


async def _validate_snapshots(
    mutations: tuple[RewindMutationRecord, ...], snapshots: object
) -> dict[str, tuple[str, str]]:
    baselines: dict[str, tuple[str, str]] = {}
    for item in mutations:
        try:
            handle = SnapshotHandle.from_dict(_thaw(item.snapshot_handle))
            snapshot = await asyncio.to_thread(snapshots.load, handle)
            _validate_snapshot(item, snapshot)
        except SnapshotMissingError:
            raise
        except SnapshotIntegrityError:
            raise
        except (TypeError, ValueError) as error:
            raise SnapshotIntegrityError("invalid snapshot handle") from error
        for path in item.paths:
            baselines.setdefault(
                os.path.normcase(path.path), (path.path, path.baseline.value)
            )
    return baselines


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _validate_snapshot(
    mutation: RewindMutationRecord, snapshot: WorkspaceSnapshot
) -> None:
    if type(snapshot) is not WorkspaceSnapshot:
        raise TypeError("snapshot load must return WorkspaceSnapshot")
    expected = {os.path.normcase(item.path): item for item in mutation.paths}
    if len(expected) != len(mutation.paths):
        raise ValueError("mutation paths alias each other")
    if len(snapshot.entries) != len(expected):
        raise ValueError("snapshot path count mismatch")
    seen: set[str] = set()
    for entry in snapshot.entries:
        if type(entry) is not SnapshotEntry:
            raise TypeError("snapshot contains invalid entry")
        identity = os.path.normcase(entry.relative_path)
        if identity in seen or identity not in expected:
            raise ValueError("snapshot path mismatch")
        seen.add(identity)
        path = expected[identity]
        actual = None if entry.content is None else hashlib.sha256(entry.content).hexdigest()
        if entry.existed != path.before_existed or actual != path.before_sha256:
            raise ValueError("snapshot preimage mismatch")


class _WorkspaceConflict(RuntimeError):
    pass


def _path_identities(mutations: tuple[RewindMutationRecord, ...]) -> set[str]:
    return {
        os.path.normcase(path.path)
        for item in mutations
        for path in item.paths
    }


def _validate_foreign_overlap(
    all_mutations: tuple[RewindMutationRecord, ...],
    owned: tuple[RewindMutationRecord, ...],
) -> None:
    owned_ids = _path_identities(owned)
    for item in all_mutations:
        if item in owned or item.status is not RewindMutationStatus.COMPLETED:
            continue
        if owned_ids & {os.path.normcase(path.path) for path in item.paths}:
            raise _WorkspaceConflict("foreign mutation overlaps owned paths")


def _validate_continuity(owned: tuple[RewindMutationRecord, ...]) -> None:
    tips: dict[str, tuple[bool, str | None]] = {}
    for item in sorted(owned, key=lambda value: value.sequence):
        for path in item.paths:
            identity = os.path.normcase(path.path)
            before = (path.before_existed, path.before_sha256)
            if identity in tips and tips[identity] != before:
                raise _WorkspaceConflict("owned mutation chain is discontinuous")
            tips[identity] = (path.after_existed, path.after_sha256)


def _validate_current_tip(
    states: tuple[WorkspaceFileState, ...],
    owned: tuple[RewindMutationRecord, ...],
) -> None:
    tips: dict[str, tuple[bool, str | None]] = {}
    for item in sorted(owned, key=lambda value: value.sequence):
        for path in item.paths:
            tips[os.path.normcase(path.path)] = (
                path.after_existed, path.after_sha256
            )
    for state in states:
        actual = (state.existed, state.sha256)
        if actual != tips[os.path.normcase(state.relative_path)]:
            raise _WorkspaceConflict("workspace tip changed")


def states_equal(
    left: tuple[WorkspaceFileState, ...],
    right: tuple[WorkspaceFileState, ...],
) -> bool:
    return left == right
