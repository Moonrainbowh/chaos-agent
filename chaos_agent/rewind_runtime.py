from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from code_agent.interfaces.rewind_models import (
    RewindCheckpointPage, RewindDisabledReason, RewindKind, RewindPreview,
)
from code_agent.interfaces.rewind_view import build_rewind_preview
from code_agent.sessions.errors import SessionNotFound, SessionStorageError
from code_agent.sessions.rewind_models import CoverageToken, RewindReadLimits
from code_agent.workspace.rewind_state import observe_file_states
from code_agent.workspace.errors import FileTooLargeError
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent.workspace.edits import WorkspaceEditor

from ._rewind_runtime_projection import (
    candidate_page, conversation_projection, global_facts, ordinary_facts,
    stable_conversation, stable_full,
)
from ._rewind_runtime_validation import (
    CodeProjection, code_precheck, project_code, states_equal,
)


class RewindRuntime:
    """Build bounded, stable rewind previews without exposing mutation APIs."""

    def __init__(
        self,
        sessions: object,
        snapshots: WorkspaceSnapshotStore,
        editor: WorkspaceEditor,
        *,
        limits: RewindReadLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(snapshots, WorkspaceSnapshotStore):
            raise TypeError("snapshots must be a WorkspaceSnapshotStore")
        if type(editor) is not WorkspaceEditor:
            raise TypeError("editor must be a WorkspaceEditor")
        self.sessions = sessions
        self.snapshots = snapshots
        self.editor = editor
        self.limits = limits or RewindReadLimits()
        if not isinstance(self.limits, RewindReadLimits):
            raise TypeError("limits must be RewindReadLimits")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def list_candidates(
        self, thread_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> RewindCheckpointPage:
        source = await self.sessions.list_rewind_candidates(
            thread_id, cursor=cursor, limit=limit
        )
        return candidate_page(source)

    async def preview(
        self, thread_id: str, checkpoint_id: str, kind: RewindKind
    ) -> RewindPreview:
        if type(kind) is not RewindKind:
            raise TypeError("kind must be a RewindKind")
        for attempt in range(2):
            try:
                result = await self._attempt(thread_id, checkpoint_id, kind)
            except _CheckpointMissing:
                timestamp = self._utc_now()
                facts = global_facts(
                    checkpoint_id, "checkpoint unavailable",
                    RewindDisabledReason.CHECKPOINT_NOT_FOUND, timestamp,
                )
                return build_rewind_preview(kind, facts)
            if result is not None:
                return result
            if attempt == 1:
                timestamp = self._utc_now()
                facts = global_facts(
                    checkpoint_id, "checkpoint changed during preview",
                    RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW, timestamp,
                )
                return build_rewind_preview(kind, facts)
        raise AssertionError("unreachable")

    async def _attempt(
        self, thread_id: str, checkpoint_id: str, kind: RewindKind
    ) -> RewindPreview | None:
        first = await self._observe(thread_id, checkpoint_id)
        conversation_count, conversation_reason = conversation_projection(first)
        code = await self._first_code(first, kind)
        probe = await self._first_limit_probe(first, kind, code)
        second = await self._observe(thread_id, checkpoint_id)
        if not self._observations_stable(first, second, kind):
            return None
        if probe is not None:
            code = await self._finish_limit_probe(second, probe)
        if code.reason is None and self._selects_code(kind):
            try:
                states = await asyncio.to_thread(
                    observe_file_states, self.editor,
                    tuple(item.path for item in code.paths),
                )
            except FileTooLargeError:
                code = CodeProjection(
                    (), (), None, RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
                )
                return self._build(
                    first, second, kind, conversation_count,
                    conversation_reason, code,
                )
            if not states_equal(code.states, states):
                return None
        return self._build(first, second, kind, conversation_count,
                           conversation_reason, code)

    async def _first_code(
        self, observation: object, kind: RewindKind
    ) -> CodeProjection:
        if not self._selects_code(kind):
            return CodeProjection((), (), None, None)
        return await project_code(observation, self.snapshots, self.editor)

    async def _first_limit_probe(
        self, observation: object, kind: RewindKind, code: CodeProjection
    ) -> tuple[CoverageToken, str, bool, bool] | None:
        if (
            not self._selects_code(kind)
            or not observation.limit_exceeded
            or code_precheck(observation, self.snapshots.workspace_fingerprint)
            is not RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
        ):
            return None
        fact = observation.checkpoint_fact
        assert fact is not None
        token = CoverageToken(
            fact.workspace_fingerprint, observation.heads.coverage_generation
        )
        try:
            anchor = await self.sessions.get_rewind_checkpoint_anchor(
                token, fact.owner_thread_id
            )
            failed = False
            moved = not _anchor_matches(anchor, token, fact.owner_thread_id,
                                        observation.heads)
        except SessionStorageError:
            failed = True
            moved = False
        return token, fact.owner_thread_id, failed, moved

    async def _finish_limit_probe(
        self, observation: object, probe: tuple[CoverageToken, str, bool, bool]
    ) -> CodeProjection:
        token, owner, first_failed, first_moved = probe
        if first_moved:
            return CodeProjection((), (), None, _MOVED)
        moved = CoverageToken(
            token.workspace_fingerprint, observation.heads.coverage_generation
        )
        if moved != token:
            return CodeProjection((), (), None, _MOVED)
        try:
            anchor = await self.sessions.get_rewind_checkpoint_anchor(token, owner)
            second_failed = False
            second_moved = not _anchor_matches(
                anchor, token, owner, observation.heads
            )
        except SessionStorageError:
            second_failed = True
            second_moved = False
        if second_moved:
            return CodeProjection((), (), None, _MOVED)
        reason = (
            RewindDisabledReason.PENDING_WORKSPACE_MUTATION
            if first_failed and second_failed
            else RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
        )
        if first_failed != second_failed:
            return CodeProjection((), (), None, _MOVED)
        return CodeProjection((), (), None, reason)

    def _observations_stable(self, first: object, second: object,
                             kind: RewindKind) -> bool:
        current = (
            self._selects_code(kind)
            and any(
                observation.checkpoint_fact is not None
                and observation.checkpoint_fact.workspace_fingerprint
                == self.snapshots.workspace_fingerprint
                for observation in (first, second)
            )
        )
        return stable_full(first, second) if current else stable_conversation(first, second)

    def _build(self, first: object, final: object, kind: RewindKind,
               count: int, conversation_reason: object,
               code: CodeProjection) -> RewindPreview | None:
        if code.reason is _MOVED:
            return None
        selects_conversation = kind in (RewindKind.CONVERSATION, RewindKind.BOTH)
        selects_code = self._selects_code(kind)
        fact = final.checkpoint_fact
        include_heads = bool(
            selects_code and fact is not None
            and fact.workspace_fingerprint == self.snapshots.workspace_fingerprint
        )
        facts = ordinary_facts(
            final, self._utc_now(),
            conversation_messages=count if selects_conversation else 0,
            conversation_reason=conversation_reason,
            code_paths=code.paths if selects_code else (),
            code_reason=code.reason if selects_code else None,
            digest=code.digest if selects_code and code.reason is None else None,
            include_code_heads=include_heads,
        )
        return build_rewind_preview(kind, facts)

    @staticmethod
    def _selects_code(kind: RewindKind) -> bool:
        return kind in (RewindKind.CODE, RewindKind.BOTH)

    async def _observe(self, thread_id: str, checkpoint_id: str) -> object:
        try:
            return await self.sessions.observe_rewind(
                thread_id, checkpoint_id, self.limits
            )
        except SessionNotFound as error:
            raise _CheckpointMissing from error

    def _utc_now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


_MOVED = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW


class _CheckpointMissing(RuntimeError):
    pass


def _anchor_matches(anchor: object, token: CoverageToken, owner: str,
                    heads: object) -> bool:
    return (
        getattr(anchor, "coverage", None) == token
        and getattr(anchor, "owner_thread_id", None) == owner
        and getattr(anchor, "coverage_state", None) == heads.coverage_state
        and getattr(anchor, "mutation_sequence", None) == heads.mutation_sequence
    )
