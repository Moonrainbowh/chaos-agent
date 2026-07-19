from __future__ import annotations
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from code_agent.interfaces.rewind_models import RewindDisabledReason, RewindKind
from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions._rewind_codec import encode_rewind_cursor
from code_agent.sessions._codec import encode_datetime
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.rewind_models import (
    CoverageToken, RewindBaseline, RewindCandidate, RewindCandidatePage,
    RewindCheckpointFact, RewindCoverageState, RewindMutationPath,
    RewindCheckpointAnchor, RewindMutationRecord, RewindMutationStatus, RewindObservation,
    RewindObservationHeads,
)
from code_agent.workspace.edits import SnapshotEntry, WorkspaceEditor, WorkspaceSnapshot
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.rewind_state import WorkspaceFileState
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_runtime import RewindRuntime
NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
def checkpoint(*, bound: int | None = 0, thread: str = "thread") -> CheckpointRecord:
    return CheckpointRecord("cp", thread, "checkpoint", {}, NOW, bound, 0)
def heads(*, messages: int = 0, events: int = 0, mutations: int = 0,
          generation: int | None = 1,
          state: RewindCoverageState | None = RewindCoverageState.ACTIVE
          ) -> RewindObservationHeads:
    return RewindObservationHeads(messages, events, mutations, generation, state)
def fact(fingerprint: str, *, owner: str = "thread",
         mutations: int = 0) -> RewindCheckpointFact:
    return RewindCheckpointFact(
        "cp", owner, CoverageToken(fingerprint, 1), 0, 0,
        RewindCoverageState.ACTIVE, NOW,
    )


def path_fact(path: str, before: bytes | None, after: bytes | None,
              baseline: RewindBaseline = RewindBaseline.UNKNOWN
              ) -> RewindMutationPath:
    return RewindMutationPath(
        path, before is not None, None if before is None else digest(before),
        baseline, after is not None, None if after is None else digest(after),
    )


def mutation(token: CoverageToken, handle: object, paths: tuple[RewindMutationPath, ...],
             *, sequence: int = 1, owner: str = "thread",
             status: RewindMutationStatus = RewindMutationStatus.COMPLETED
             ) -> RewindMutationRecord:
    completed = NOW if status is not RewindMutationStatus.PREPARED else None
    return RewindMutationRecord(
        f"m{sequence}", sequence, token, owner, owner, None, None,
        f"r{sequence}", "write_file", status, None, handle, paths, NOW, completed,
    )


def observation(fingerprint: str, *, cp: CheckpointRecord | None = None,
                anchor: RewindCheckpointFact | None | object = ...,
                count: int | None = 0, head: RewindObservationHeads | None = None,
                mutations: tuple[RewindMutationRecord, ...] = (),
                limited: bool = False) -> RewindObservation:
    checkpoint_fact = fact(fingerprint) if anchor is ... else anchor
    return RewindObservation(
        cp or checkpoint(), checkpoint_fact, count, head or heads(),
        mutations, limited,
    )


class FakeSessions:
    def __init__(self, observations=(), *, page=None) -> None:
        self.observations = list(observations)
        self.page = page or RewindCandidatePage((), None)
        self.calls: list[tuple[object, ...]] = []
        self.anchor_outcomes: list[object] = []

    async def observe_rewind(self, thread, checkpoint_id, limits):
        self.calls.append(("observe", thread, checkpoint_id, limits))
        if not self.observations:
            raise SessionNotFound("checkpoint")
        value = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
        if isinstance(value, BaseException):
            raise value
        return value

    async def list_rewind_candidates(self, thread, *, cursor=None, limit=20):
        self.calls.append(("list", thread, cursor, limit))
        return self.page

    async def get_rewind_checkpoint_anchor(self, token, owner):
        self.calls.append(("anchor", token, owner))
        value = (
            self.anchor_outcomes.pop(0) if self.anchor_outcomes
            else RewindCheckpointAnchor(
                token, owner, RewindCoverageState.ACTIVE, 2
            )
        )
        if isinstance(value, BaseException):
            raise value
        return value


class RuntimeHarness:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        self.workspace = workspace
        guard = WorkspacePathGuard(workspace)
        self.editor = WorkspaceEditor(guard)
        self.snapshots = WorkspaceSnapshotStore(guard, root / "state")
        self.fingerprint = self.snapshots.workspace_fingerprint

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self, observations=(), **kwargs) -> RewindRuntime:
        sessions = FakeSessions(observations, **kwargs)
        self.sessions = sessions
        return RewindRuntime(
            sessions, self.snapshots, self.editor, clock=lambda: NOW
        )


class RewindRuntimeContractTests(RuntimeHarness, unittest.IsolatedAsyncioTestCase):
    async def test_candidate_fields_cursor_and_limit_map_unchanged(self) -> None:
        source = RewindCandidate("cp", "label", NOW, True, False)
        cursor = encode_rewind_cursor(encode_datetime(NOW), "cp")
        runtime = self.runtime(page=RewindCandidatePage((source,), cursor))
        page = await runtime.list_candidates("thread", cursor=cursor, limit=7)
        self.assertEqual(page.items[0].__dict__, {
            "checkpoint_id": "cp", "label": "label", "created_at": NOW,
            "has_message_bound": True, "has_code_anchor": False,
        })
        self.assertEqual((page.next_cursor, self.sessions.calls[-1][1:]), (
            cursor, ("thread", cursor, 7),
        ))

    async def test_legacy_checkpoint_previews_conversation_only(self) -> None:
        legacy = observation(
            self.fingerprint, anchor=None, count=3,
            head=heads(messages=5, events=4, generation=None, state=None),
        )
        preview = await self.runtime((legacy, legacy)).preview(
            "thread", "cp", RewindKind.BOTH
        )
        self.assertEqual(preview.conversation_messages, 3)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE,
        ))

    async def test_missing_and_invalid_conversation_bounds_map_exactly(self) -> None:
        cases = (
            (checkpoint(bound=None), None, RewindDisabledReason.MESSAGE_BOUND_MISSING),
            (checkpoint(bound=4), None, RewindDisabledReason.MESSAGE_BOUND_INVALID),
        )
        for cp, count, reason in cases:
            with self.subTest(reason=reason):
                item = observation(
                    self.fingerprint, cp=cp, count=count,
                    head=heads(messages=3),
                )
                preview = await self.runtime((item, item)).preview(
                    "thread", "cp", RewindKind.CONVERSATION
                )
                self.assertEqual(preview.disabled_reasons, (reason,))

    async def test_cross_thread_checkpoint_maps_to_not_found(self) -> None:
        preview = await self.runtime().preview("other", "cp", RewindKind.BOTH)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        ))
        self.assertEqual(preview.as_of.message_sequence, None)

    async def test_zero_message_conversation_is_enabled(self) -> None:
        item = observation(self.fingerprint, anchor=None, count=0,
                           head=heads(generation=None, state=None))
        preview = await self.runtime((item, item)).preview(
            "thread", "cp", RewindKind.CONVERSATION
        )
        self.assertTrue(preview.enabled)
        self.assertEqual(preview.conversation_messages, 0)

    async def test_zero_mutation_code_is_enabled_with_empty_digest(self) -> None:
        item = observation(self.fingerprint)
        preview = await self.runtime((item, item)).preview(
            "thread", "cp", RewindKind.CODE
        )
        self.assertTrue(preview.enabled)
        self.assertEqual(preview.as_of.relevant_path_digest, digest(b"[]"))

    async def test_code_only_ignores_message_bound_failure(self) -> None:
        item = observation(self.fingerprint, cp=checkpoint(bound=None), count=None)
        preview = await self.runtime((item, item)).preview(
            "thread", "cp", RewindKind.CODE
        )
        self.assertTrue(preview.enabled)

    def test_public_runtime_has_no_mutating_api(self) -> None:
        self.assertTrue(
            {"apply", "restore", "confirm", "reset", "delete"}.isdisjoint(
                dir(RewindRuntime)
            )
        )
        class DerivedEditor(WorkspaceEditor):
            pass
        with self.assertRaises(TypeError):
            RewindRuntime(
                FakeSessions(), self.snapshots, DerivedEditor(self.editor.guard))


class RewindRuntimeOwnershipTests(RuntimeHarness, unittest.IsolatedAsyncioTestCase):
    async def test_exact_path_state_move_retries_complete_attempt(self):
        (self.workspace / "note.txt").write_bytes(b"after")
        handle = self.snapshots.save(WorkspaceSnapshot((
            SnapshotEntry("note.txt", b"before", True),)))
        record = mutation(
            CoverageToken(self.fingerprint, 1), handle.to_dict(),
            (path_fact("note.txt", b"before", b"after"),))
        item = observation(
            self.fingerprint, head=heads(mutations=1), mutations=(record,))
        runtime = self.runtime((item, item, item, item))
        moved = WorkspaceFileState(
            "note.txt", True, digest(b"moved"), len(b"moved"))
        stable = WorkspaceFileState(
            "note.txt", True, digest(b"after"), len(b"after"))
        with patch(
            "code_agent_win.rewind_runtime.observe_file_states",
            side_effect=((moved,), (stable,)),
        ):
            preview = await runtime.preview("thread", "cp", RewindKind.CODE)
        self.assertTrue(preview.enabled)
        self.assertEqual(sum(c[0] == "observe" for c in self.sessions.calls), 4)

    async def test_child_checkpoint_selects_root_owned_effects_and_earliest_baseline(self):
        path = self.workspace / "note.txt"
        path.write_bytes(b"after")
        old = self.snapshots.save(WorkspaceSnapshot((
            SnapshotEntry("note.txt", b"before", True),
        )))
        token = CoverageToken(self.fingerprint, 1)
        record = mutation(
            token, old.to_dict(),
            (path_fact("note.txt", b"before", b"after",
                       RewindBaseline.GIT_UNSTAGED),),
            owner="root",
        )
        item = observation(
            self.fingerprint, anchor=fact(self.fingerprint, owner="root"),
            head=heads(mutations=1), mutations=(record,),
        )
        preview = await self.runtime((item, item)).preview(
            "child", "cp", RewindKind.CODE
        )
        self.assertEqual(
            [(p.path, p.baseline_provenance, p.preserves_pre_agent_baseline)
             for p in preview.code_paths],
            [("note.txt", RewindBaseline.GIT_UNSTAGED.value, True)],
        )

    async def test_owner_case_alias_chain_keeps_earliest_spelling(self):
        (self.workspace / "Name.txt").write_bytes(b"new")
        (self.workspace / "a.txt").write_bytes(b"a-new")
        token = CoverageToken(self.fingerprint, 1)
        old = self.snapshots.save(WorkspaceSnapshot((
            SnapshotEntry("Name.txt", b"old", True),
            SnapshotEntry("a.txt", b"a-old", True),)))
        middle = self.snapshots.save(WorkspaceSnapshot((
            SnapshotEntry("Name.txt", b"middle", True),)))
        records = (
            mutation(token, old.to_dict(), (
                path_fact("Name.txt", b"old", b"middle",
                          RewindBaseline.NON_GIT_EXISTING),
                path_fact("a.txt", b"a-old", b"a-new"))),
            mutation(token, middle.to_dict(), (
                path_fact("name.txt", b"middle", b"new"),), sequence=2),
        )
        item = observation(
            self.fingerprint, head=heads(mutations=2), mutations=records[::-1])
        with patch(
            "code_agent_win._rewind_runtime_validation.os.path.normcase",
            side_effect=lambda value: value.lower(),
        ):
            preview = await self.runtime((item, item)).preview(
                "thread", "cp", RewindKind.CODE)
        self.assertTrue(preview.enabled)
        self.assertEqual(tuple(p.path for p in preview.code_paths),
                         ("a.txt", "Name.txt"))
        self.assertEqual(preview.code_paths[1].baseline_provenance,
                         RewindBaseline.NON_GIT_EXISTING.value)


if __name__ == "__main__":
    unittest.main()
