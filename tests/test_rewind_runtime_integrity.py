from __future__ import annotations

import unittest
from unittest.mock import patch
from code_agent.interfaces.rewind_models import RewindDisabledReason, RewindKind
from code_agent.sessions.errors import SessionStorageError
from code_agent.sessions.rewind_models import (
    CoverageToken, RewindBaseline, RewindGapPrepare, RewindMutationPrepare,
    RewindMutationRecord,
    RewindMutationStatus, RewindReadLimits,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot
from tests.test_rewind_runtime import (
    NOW, RuntimeHarness, digest, fact, heads, mutation, observation, path_fact,
)
def gap(token: CoverageToken, *, sequence: int = 1) -> RewindMutationRecord:
    return RewindMutationRecord(
        f"g{sequence}", sequence, token, "thread", "thread", None, None,
        f"gap{sequence}", "plugin", RewindMutationStatus.GAP, "unknown write",
        None, (), NOW, NOW,
    )
class IntegrityHarness(RuntimeHarness):
    def save(self, path: str, content: bytes | None):
        return self.snapshots.save(WorkspaceSnapshot((
            SnapshotEntry(path, content, content is not None),
        )))
    async def preview_records(self, records, *, anchor=None, head=None, limited=False):
        item = observation(
            self.fingerprint,
            anchor=fact(self.fingerprint) if anchor is None else anchor,
            head=head or heads(mutations=max((r.sequence for r in records), default=0)),
            mutations=tuple(records), limited=limited,
        )
        return await self.runtime((item, item)).preview(
            "thread", "cp", RewindKind.CODE
        )
    def completed(self, path, before, after, *, sequence=1, owner="thread",
                  baseline=RewindBaseline.UNKNOWN):
        handle = self.save(path, before)
        return mutation(
            CoverageToken(self.fingerprint, 1), handle.to_dict(),
            (path_fact(path, before, after, baseline),),
            sequence=sequence, owner=owner,
        )
class RewindRuntimeIntegrityTests(
    IntegrityHarness, unittest.IsolatedAsyncioTestCase
):
    async def test_old_middle_same_path_reverse_chain_and_earliest_baseline(self):
        (self.workspace / "note.txt").write_bytes(b"new")
        first = self.completed(
            "note.txt", b"old", b"middle", sequence=1,
            baseline=RewindBaseline.NON_GIT_EXISTING,
        )
        second = self.completed("note.txt", b"middle", b"new", sequence=2)
        preview = await self.preview_records((first, second))
        self.assertTrue(preview.enabled)
        self.assertEqual(preview.code_paths[0].baseline_provenance,
                         RewindBaseline.NON_GIT_EXISTING.value)
    async def test_real_frozen_sessions_handle_is_recursively_thawed(self):
        (self.workspace / "a.txt").write_bytes(b"after")
        record = self.completed("a.txt", b"before", b"after")
        self.assertIsInstance(record.snapshot_handle["paths"], tuple)
        self.assertTrue((await self.preview_records((record,))).enabled)
    async def test_missing_manifest_maps_snapshot_missing(self):
        (self.workspace / "a.txt").write_bytes(b"after")
        record = self.completed("a.txt", b"before", b"after")
        identifier = record.snapshot_handle["identifier"]
        (self.snapshots.root / "manifests" / f"{identifier}.json").unlink()
        preview = await self.preview_records((record,))
        self.assertEqual(preview.disabled_reasons,
                         (RewindDisabledReason.SNAPSHOT_MISSING,))
    async def test_corrupt_manifest_maps_snapshot_invalid(self):
        (self.workspace / "a.txt").write_bytes(b"after")
        record = self.completed("a.txt", b"before", b"after")
        identifier = record.snapshot_handle["identifier"]
        path = self.snapshots.root / "manifests" / f"{identifier}.json"
        path.write_bytes(b"corrupt")
        preview = await self.preview_records((record,))
        self.assertEqual(preview.disabled_reasons,
                         (RewindDisabledReason.SNAPSHOT_INVALID,))

    async def test_malformed_handle_and_preimage_mismatch_map_snapshot_invalid(self):
        token = CoverageToken(self.fingerprint, 1)
        malformed = mutation(
            token, {"unexpected": ["nested"]},
            (path_fact("a.txt", b"before", b"after"),),
        )
        (self.workspace / "a.txt").write_bytes(b"after")
        mismatch = mutation(
            token, self.save("a.txt", b"wrong").to_dict(),
            (path_fact("a.txt", b"before", b"after"),),
        )
        wrong_path = mutation(
            token, self.save("b.txt", b"before").to_dict(),
            (path_fact("a.txt", b"before", b"after"),),
        )
        aliases = mutation(
            token, self.save("a.txt", b"middle").to_dict(),
            (path_fact("a.txt", b"before", b"middle"),
             path_fact("A.txt", b"middle", b"after")),
        )
        for record in (malformed, mismatch, wrong_path, aliases):
            with self.subTest(record=record.mutation_id):
                with patch(
                    "code_agent_win._rewind_runtime_validation.os.path.normcase",
                    side_effect=lambda value: value.lower(),
                ):
                    preview = await self.preview_records((record,))
                self.assertEqual(preview.disabled_reasons,
                                 (RewindDisabledReason.SNAPSHOT_INVALID,))

    async def test_gap_and_prepared_have_stable_precedence(self):
        token = CoverageToken(self.fingerprint, 1)
        prepared = mutation(
            token, self.save("a.txt", b"old").to_dict(),
            (path_fact("a.txt", b"old", b"new"),),
            sequence=2, status=RewindMutationStatus.PREPARED,
        )
        cases = (
            ((prepared,), RewindDisabledReason.PENDING_WORKSPACE_MUTATION),
            ((gap(token),), RewindDisabledReason.CODE_JOURNAL_INCOMPLETE),
            ((gap(token), prepared), RewindDisabledReason.CODE_JOURNAL_INCOMPLETE),
        )
        for records, reason in cases:
            with self.subTest(reason=reason):
                preview = await self.preview_records(records)
                self.assertEqual(preview.disabled_reasons, (reason,))

    async def test_foreign_disjoint_is_ignored_but_overlap_conflicts(self):
        (self.workspace / "owned.txt").write_bytes(b"new")
        owned = self.completed("owned.txt", b"old", b"new")
        disjoint = self.completed(
            "other.txt", b"x", b"y", sequence=2, owner="foreign"
        )
        overlap = self.completed(
            "owned.txt", b"new", b"later", sequence=2, owner="foreign"
        )
        self.assertTrue((await self.preview_records((owned, disjoint))).enabled)
        preview = await self.preview_records((owned, overlap))
        self.assertEqual(preview.disabled_reasons,
                         (RewindDisabledReason.WORKSPACE_CONFLICT,))

    async def test_windows_case_alias_overlap_conflicts(self):
        (self.workspace / "Name.txt").write_bytes(b"new")
        owned = self.completed("Name.txt", b"old", b"new")
        foreign = mutation(
            CoverageToken(self.fingerprint, 1), owned.snapshot_handle,
            (path_fact("name.txt", b"x", b"y"),),
            sequence=2, owner="foreign",
        )
        with patch(
            "code_agent_win._rewind_runtime_validation.os.path.normcase",
            side_effect=lambda value: value.lower(),
        ):
            preview = await self.preview_records((owned, foreign))
        self.assertEqual(preview.disabled_reasons,
                         (RewindDisabledReason.WORKSPACE_CONFLICT,))

    async def test_same_path_discontinuity_and_current_tip_conflict(self):
        (self.workspace / "a.txt").write_bytes(b"user")
        first = self.completed("a.txt", b"old", b"middle")
        broken = self.completed("a.txt", b"other", b"new", sequence=2)
        for records in ((first, broken), (first,)):
            with self.subTest(count=len(records)):
                preview = await self.preview_records(records)
                self.assertEqual(preview.disabled_reasons,
                                 (RewindDisabledReason.WORKSPACE_CONFLICT,))

    async def test_missing_blob_and_foreign_manifest_fail_closed(self):
        (self.workspace / "a.txt").write_bytes(b"after")
        missing = self.completed("a.txt", b"before", b"after")
        (self.snapshots.root / "blobs" / digest(b"before")).unlink()
        self.assertEqual(
            (await self.preview_records((missing,))).disabled_reasons,
            (RewindDisabledReason.SNAPSHOT_MISSING,),
        )
        foreign = self.completed("a.txt", b"foreign", b"after")
        identifier = foreign.snapshot_handle["identifier"]
        manifest = self.snapshots.root / "manifests" / f"{identifier}.json"
        payload = manifest.read_bytes().replace(
            self.fingerprint.encode(), b"f" * 64
        )
        manifest.write_bytes(payload)
        changed = dict(foreign.snapshot_handle)
        changed["digest"] = digest(payload)
        record = mutation(
            CoverageToken(self.fingerprint, 1), changed, foreign.paths
        )
        self.assertEqual(
            (await self.preview_records((record,))).disabled_reasons,
            (RewindDisabledReason.SNAPSHOT_INVALID,),
        )

    async def _limited_repository(self, name: str):
        repository = RewindSessionRepository(
            self.workspace.parent / f"{name}.sqlite3"
        )
        owner = await repository.create_thread()
        coverage = await repository.ensure_rewind_coverage(self.fingerprint)
        anchor = await repository.get_rewind_checkpoint_anchor(
            coverage.token, owner
        )
        checkpoint_id = await repository.create_checkpoint(
            owner, "before", rewind_anchor=anchor
        )
        records = []
        for sequence, path in enumerate(("a.txt", "b.txt"), 1):
            request = RewindMutationPrepare(
                coverage.token, owner, owner, None, None, f"request-{sequence}",
                "write_file", {"identifier": f"snapshot-{sequence}"},
                (path_fact(path, None, f"after-{sequence}".encode()),),
            )
            records.append(await repository.prepare_rewind_mutation(request))
            if sequence == 1:
                await repository.complete_rewind_mutation(
                    records[-1].mutation_id
                )
        return repository, owner, checkpoint_id, records[-1]

    async def test_real_repository_prepared_plus_limit_is_pending(self):
        repository, owner, checkpoint_id, _ = await self._limited_repository(
            "prepared-limit"
        )
        runtime = self.runtime()
        runtime.sessions = repository
        runtime.limits = RewindReadLimits(1, 10)
        preview = await runtime.preview(owner, checkpoint_id, RewindKind.CODE)
        self.assertEqual(preview.disabled_reasons,
                         (RewindDisabledReason.PENDING_WORKSPACE_MUTATION,))

    async def test_real_repository_gap_plus_limit_precedes_limit(self):
        repository, owner, checkpoint_id, prepared = (
            await self._limited_repository("gap-limit")
        )
        await repository.abort_rewind_mutation(prepared.mutation_id)
        await repository.record_rewind_gap(RewindGapPrepare(
            prepared.coverage, owner, owner, None, None, "gap-request",
            "plugin_write", "unknown write",
        ))
        runtime = self.runtime()
        runtime.sessions = repository
        runtime.limits = RewindReadLimits(1, 10)
        preview = await runtime.preview(owner, checkpoint_id, RewindKind.CODE)
        self.assertEqual(preview.code_paths, ())
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.CODE_JOURNAL_INCOMPLETE,))

    async def test_real_repository_prepared_settles_between_probes_retries(self):
        for operation in ("complete", "abort"):
            with self.subTest(operation=operation):
                repository, owner, checkpoint_id, prepared = (
                    await self._limited_repository(f"settle-{operation}")
                )

                class SettlingSessions:
                    def __init__(self):
                        self.anchor_calls = 0
                        self.observe_calls = 0

                    def __getattr__(self, name):
                        return getattr(repository, name)

                    async def observe_rewind(self, *args):
                        self.observe_calls += 1
                        return await repository.observe_rewind(*args)

                    async def get_rewind_checkpoint_anchor(self, *args):
                        self.anchor_calls += 1
                        try:
                            return await repository.get_rewind_checkpoint_anchor(*args)
                        except SessionStorageError:
                            method = getattr(
                                repository, f"{operation}_rewind_mutation"
                            )
                            await method(prepared.mutation_id)
                            raise

                sessions = SettlingSessions()
                runtime = self.runtime()
                runtime.sessions = sessions
                runtime.limits = RewindReadLimits(1, 10)
                preview = await runtime.preview(
                    owner, checkpoint_id, RewindKind.CODE
                )
                self.assertEqual(
                    preview.disabled_reasons,
                    (RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,),
                )
                self.assertEqual(sessions.observe_calls, 4)
                runtime.limits = RewindReadLimits(10, 1)
                limited = await runtime.preview(
                    owner, checkpoint_id, RewindKind.CODE)
                self.assertEqual(limited.code_paths, ())
                self.assertEqual(limited.disabled_reasons, (
                    RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,))


if __name__ == "__main__":
    unittest.main()
