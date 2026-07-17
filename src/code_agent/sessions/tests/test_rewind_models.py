from __future__ import annotations

import base64
import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._rewind_codec import (  # noqa: E402
    decode_rewind_cursor,
    decode_rewind_handle,
    encode_rewind_cursor,
    encode_rewind_handle,
)
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.models import CheckpointRecord  # noqa: E402
from code_agent.sessions.rewind_models import (  # noqa: E402
    DEFAULT_REWIND_MAX_MUTATIONS,
    CoverageToken,
    RewindBaseline,
    RewindCandidate,
    RewindCandidatePage,
    RewindCheckpointFact,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationPrepare,
    RewindMutationStatus,
    RewindObservation,
    RewindObservationHeads,
    RewindReadLimits,
)


NOW = datetime(2026, 7, 17, 8, 30, tzinfo=timezone(timedelta(hours=8)))
HASH = "a" * 64


def _path(path: str = "src/main.py") -> RewindMutationPath:
    return RewindMutationPath(
        path, True, HASH, RewindBaseline.GIT_UNSTAGED, False, None
    )


def _prepare(handle: object, paths: object) -> RewindMutationPrepare:
    return RewindMutationPrepare(
        CoverageToken("b" * 64, 1),
        "root-thread",
        "child-thread",
        "task-1",
        None,
        "request-1",
        "write_file",
        handle,  # type: ignore[arg-type]
        paths,  # type: ignore[arg-type]
    )


class RewindModelTests(unittest.TestCase):
    def test_enum_values_stable(self) -> None:
        self.assertEqual(
            [item.value for item in RewindCoverageState],
            ["active", "invalidated"],
        )
        self.assertEqual(
            [item.value for item in RewindMutationStatus],
            ["prepared", "completed", "aborted", "gap"],
        )
        self.assertEqual(
            [item.value for item in RewindBaseline],
            [
                "git-staged",
                "git-unstaged",
                "git-untracked",
                "non-git-existing",
                "absent",
                "unknown",
            ],
        )

    def test_token_and_limits_reject_invalid_bounds(self) -> None:
        token = CoverageToken("a" * 64, 1)
        self.assertEqual(token.workspace_fingerprint, "a" * 64)
        for generation in (True, 0, -1):
            with self.assertRaises((TypeError, ValueError)):
                CoverageToken("a" * 64, generation)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CoverageToken("A" * 64, 1)
        self.assertEqual(
            RewindReadLimits().max_mutations, DEFAULT_REWIND_MAX_MUTATIONS
        )
        for value in (True, 0, DEFAULT_REWIND_MAX_MUTATIONS + 1):
            with self.assertRaises((TypeError, ValueError)):
                RewindReadLimits(max_mutations=value)  # type: ignore[arg-type]

    def test_path_fact_enforces_canonical_existence_hash_baseline(self) -> None:
        self.assertEqual(_path().path, "src/main.py")
        invalid = [
            ("/abs.py", True, HASH, RewindBaseline.GIT_STAGED),
            ("C:/outside.txt", True, HASH, RewindBaseline.GIT_STAGED),
            ("src/\0main.py", True, HASH, RewindBaseline.GIT_STAGED),
            ("src\\main.py", True, HASH, RewindBaseline.GIT_STAGED),
            ("src/../main.py", True, HASH, RewindBaseline.GIT_STAGED),
            ("src/main.py", False, HASH, RewindBaseline.ABSENT),
            ("src/main.py", True, None, RewindBaseline.GIT_STAGED),
            ("src/main.py", True, HASH.upper(), RewindBaseline.GIT_STAGED),
            ("src/main.py", True, HASH, RewindBaseline.ABSENT),
        ]
        for path, existed, digest, baseline in invalid:
            with self.subTest(path=path, baseline=baseline):
                with self.assertRaises((TypeError, ValueError)):
                    RewindMutationPath(
                        path, existed, digest, baseline, False, None
                    )

    def test_prepare_deep_freezes_handle_and_paths(self) -> None:
        source = {"nested": [{"value": 1}]}
        paths = (_path(),)
        prepare = _prepare(source, paths)
        source["nested"][0]["value"] = 2
        self.assertEqual(prepare.snapshot_handle["nested"][0]["value"], 1)
        self.assertIsInstance(prepare.snapshot_handle["nested"], tuple)
        self.assertEqual(prepare.paths, (_path(),))
        with self.assertRaises(TypeError):
            prepare.snapshot_handle["new"] = 1  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            prepare.action_name = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            _prepare({}, [_path()])

    def test_prepare_rejects_duplicate_paths_and_oversized_handle(self) -> None:
        with self.assertRaises(ValueError):
            _prepare({}, (_path(), _path()))
        with self.assertRaises(ValueError):
            _prepare({"large": "x" * (64 * 1024)}, (_path(),))

    def test_checkpoint_fact_separates_child_id_from_root_owner(self) -> None:
        fact = RewindCheckpointFact(
            "child-checkpoint",
            "root-owner",
            CoverageToken("c" * 64, 3),
            7,
            RewindCoverageState.ACTIVE,
            NOW,
        )
        self.assertEqual(fact.checkpoint_id, "child-checkpoint")
        self.assertEqual(fact.owner_thread_id, "root-owner")
        self.assertEqual(fact.created_at, NOW.astimezone(timezone.utc))
        self.assertEqual((fact.workspace_fingerprint, fact.generation), ("c" * 64, 3))
        checkpoint = CheckpointRecord(
            "child-checkpoint", "child-thread", "label", created_at=NOW
        )
        heads = RewindObservationHeads(0, 0, 0, None, None)
        with self.assertRaises(TypeError):
            RewindObservation(checkpoint, fact, 0, heads, [], False)  # type: ignore[arg-type]
        candidate = RewindCandidate("child-checkpoint", "label", NOW, True, True)
        with self.assertRaises(TypeError):
            RewindCandidatePage([candidate], None)  # type: ignore[arg-type]

    def test_handle_codec_is_canonical_bounded_and_deep_frozen(self) -> None:
        source = {"z": [1, {"é": True}], "a": None}
        encoded = encode_rewind_handle(source)
        self.assertEqual(encoded, '{"a":null,"z":[1,{"é":true}]}')
        decoded = decode_rewind_handle(encoded)
        source["z"][1]["é"] = False
        self.assertEqual(decoded["z"][1]["é"], True)
        self.assertIsInstance(decoded["z"], tuple)
        with self.assertRaises(SessionCorruptionError):
            decode_rewind_handle('{"z":1, "a":2}')
        with self.assertRaises(ValueError):
            encode_rewind_handle({"large": "界" * (64 * 1024)})

    def test_cursor_round_trip_and_rejects_invalid_tokens(self) -> None:
        created_at = "2026-07-17T00:30:00Z"
        token = encode_rewind_cursor(created_at, "checkpoint-1")
        self.assertEqual(
            decode_rewind_cursor(token), (created_at, "checkpoint-1")
        )
        noncanonical = base64.urlsafe_b64encode(
            json.dumps(
                {"id": "checkpoint-1", "created_at": created_at},
                separators=(", ", ": "),
            ).encode()
        ).decode().rstrip("=")
        for invalid in (
            token + "=",
            "@@@",
            "a" * 4096,
            noncanonical,
            base64.urlsafe_b64encode(b"[]").decode().rstrip("="),
        ):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaisesRegex(ValueError, "^invalid rewind cursor$"):
                    decode_rewind_cursor(invalid)

        checkpoint = CheckpointRecord(
            "checkpoint-1", "child-thread", "label", created_at=NOW
        )
        self.assertEqual(checkpoint.thread_id, "child-thread")


if __name__ == "__main__":
    unittest.main()
