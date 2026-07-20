from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import code_agent.workspace._guarded_read as guarded_read  # noqa: E402
import code_agent.workspace._snapshot_artifacts as artifact_module  # noqa: E402
from code_agent.workspace.edits import SnapshotEntry, WorkspaceEditor, WorkspaceSnapshot  # noqa: E402
from code_agent.workspace.errors import (  # noqa: E402
    FileTooLargeError,
    SnapshotIntegrityError,
    SnapshotMissingError,
    WorkspaceError,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.snapshot_store import (  # noqa: E402
    SnapshotHandle,
    WorkspaceSnapshotStore,
)


def canonical(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class SnapshotErrorClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "workspace"
        self.artifacts = self.base / "product-state"
        self.root.mkdir()
        self.guard = WorkspacePathGuard(self.root)
        self.editor = WorkspaceEditor(self.guard)
        self.store = WorkspaceSnapshotStore(self.guard, self.artifacts)
        (self.root / "file.bin").write_bytes(b"original")
        self.snapshot = self.editor.snapshot(("file.bin",))
        self.handle = self.store.save(self.snapshot)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def manifest_path(self) -> Path:
        return self.artifacts / "manifests" / f"{self.handle.identifier}.json"

    @property
    def blob_path(self) -> Path:
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        digest = payload["entries"][0]["digest"]
        return self.artifacts / "blobs" / digest

    def replace_manifest(self, raw: bytes) -> SnapshotHandle:
        self.manifest_path.write_bytes(raw)
        return replace(self.handle, digest=hashlib.sha256(raw).hexdigest())

    def manifest(self) -> dict[str, object]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_snapshot_errors_are_workspace_errors(self) -> None:
        self.assertTrue(issubclass(SnapshotMissingError, WorkspaceError))
        self.assertTrue(issubclass(SnapshotIntegrityError, WorkspaceError))

    def test_missing_manifest_raises_snapshot_missing(self) -> None:
        self.manifest_path.unlink()

        with self.assertRaises(SnapshotMissingError) as captured:
            self.store.load(self.handle)

        self.assertIn("manifest", str(captured.exception))
        self.assertNotIn(str(self.artifacts), str(captured.exception))

    def test_missing_blob_raises_snapshot_missing(self) -> None:
        self.blob_path.unlink()

        with self.assertRaises(SnapshotMissingError) as captured:
            self.store.load(self.handle)

        self.assertIn("blob", str(captured.exception))
        self.assertNotIn(str(self.artifacts), str(captured.exception))

    def test_tampered_or_noncanonical_manifest_raises_integrity(self) -> None:
        original = self.manifest_path.read_bytes()
        variants = (original + b"\n", json.dumps(self.manifest(), indent=2).encode())
        for raw in variants:
            with self.subTest(raw=raw[:20]):
                handle = self.replace_manifest(raw)
                with self.assertRaises(SnapshotIntegrityError):
                    self.store.load(handle)

    def test_manifest_decode_failures_are_integrity(self) -> None:
        variants = (b"\xff", b"{", b"[" * 2000 + b"]" * 2000)
        for raw in variants:
            with self.subTest(size=len(raw)):
                handle = self.replace_manifest(raw)
                with self.assertRaises(SnapshotIntegrityError):
                    self.store.load(handle)

    def test_tampered_blob_size_or_digest_raises_integrity(self) -> None:
        original = self.blob_path.read_bytes()
        for raw in (original + b"!", b"x" * len(original)):
            with self.subTest(size=len(raw)):
                self.blob_path.write_bytes(raw)
                with self.assertRaises(SnapshotIntegrityError):
                    self.store.load(self.handle)
                self.blob_path.write_bytes(original)

    def test_handle_manifest_mismatch_raises_integrity(self) -> None:
        handles = (
            replace(self.handle, digest="0" * 64),
            replace(self.handle, paths=("other.bin",)),
            replace(self.handle, total_bytes=self.handle.total_bytes + 1),
        )
        for handle in handles:
            with self.subTest(handle=handle):
                with self.assertRaises(SnapshotIntegrityError):
                    self.store.load(handle)

    def test_workspace_fingerprint_mismatch_raises_integrity(self) -> None:
        other_root = self.base / "other-workspace"
        other_root.mkdir()
        other = WorkspaceSnapshotStore(WorkspacePathGuard(other_root), self.artifacts)

        with self.assertRaises(SnapshotIntegrityError):
            other.load(self.handle)

    def test_manifest_structure_and_path_failures_raise_integrity(self) -> None:
        mutations = []
        extra = self.manifest()
        extra["extra"] = True
        mutations.append(extra)
        invalid_path = self.manifest()
        invalid_path["entries"][0]["path"] = "../outside.bin"  # type: ignore[index]
        mutations.append(invalid_path)
        invalid_digest = self.manifest()
        invalid_digest["entries"][0]["digest"] = "bad"  # type: ignore[index]
        mutations.append(invalid_digest)
        for payload in mutations:
            with self.subTest(payload=payload):
                handle = self.replace_manifest(canonical(payload))
                with self.assertRaises(SnapshotIntegrityError):
                    self.store.load(handle)

    def test_manifest_over_read_budget_raises_integrity(self) -> None:
        bounded = WorkspaceSnapshotStore(
            self.guard,
            self.artifacts,
            max_manifest_bytes=self.manifest_path.stat().st_size - 1,
        )

        with self.assertRaises(SnapshotIntegrityError):
            bounded.load(self.handle)

    def test_permission_link_or_identity_failure_is_not_missing(self) -> None:
        permission = WorkspaceError("snapshot artifact permission denied")
        permission.__cause__ = PermissionError("denied")
        failures = (
            permission,
            WorkspaceError("artifact path contains a link or reparse point"),
            WorkspaceError("artifact identity changed while opening"),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                with patch.object(
                    artifact_module, "read_guarded_file", side_effect=failure
                ):
                    with self.assertRaises(SnapshotIntegrityError) as captured:
                        self.store.load(self.handle)
                self.assertNotIsInstance(captured.exception, SnapshotMissingError)

    def test_disappearing_after_open_is_integrity_not_missing(self) -> None:
        with patch.object(
            guarded_read, "_verify_handle", side_effect=FileNotFoundError("gone")
        ):
            with self.assertRaises(SnapshotIntegrityError) as captured:
                self.store.load(self.handle)

        self.assertNotIsInstance(captured.exception, SnapshotMissingError)

    def test_workspace_fingerprint_property_is_stable_and_read_only(self) -> None:
        same = WorkspaceSnapshotStore(self.guard, self.artifacts)
        other_root = self.base / "another-workspace"
        other_root.mkdir()
        other = WorkspaceSnapshotStore(
            WorkspacePathGuard(other_root), self.base / "other-state"
        )

        self.assertEqual(self.store.workspace_fingerprint, same.workspace_fingerprint)
        self.assertNotEqual(self.store.workspace_fingerprint, other.workspace_fingerprint)
        with self.assertRaises(AttributeError):
            self.store.workspace_fingerprint = "0" * 64  # type: ignore[misc]

    def test_load_handle_type_misuse_remains_type_error(self) -> None:
        with self.assertRaises(TypeError):
            self.store.load(self.handle.to_dict())  # type: ignore[arg-type]

    def test_load_does_not_mask_programming_errors_as_integrity(self) -> None:
        with patch.object(
            self.store,
            "_load_referenced",
            side_effect=RuntimeError("programming error"),
        ):
            with self.assertRaises(RuntimeError):
                self.store.load(self.handle)

    def test_handle_rejects_noncanonical_windows_and_unicode_paths(self) -> None:
        for path in ("C:relative", "file.bin:stream", "bad\ud800.bin"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    replace(self.handle, paths=(path,))
        with self.assertRaises(ValueError):
            replace(self.handle, paths=("File.bin", "file.bin"))

    def test_save_type_and_budget_errors_keep_their_types(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save(object())  # type: ignore[arg-type]
        invalid = WorkspaceSnapshot(
            (
                SnapshotEntry("duplicate.bin", b"one", True),
                SnapshotEntry("duplicate.bin", b"two", True),
            )
        )
        with self.assertRaises(ValueError):
            self.store.save(invalid)
        with self.assertRaises(FileTooLargeError):
            WorkspaceSnapshotStore(
                self.guard, self.base / "small-total", max_total_bytes=1
            ).save(self.snapshot)
        with self.assertRaises(FileTooLargeError):
            WorkspaceSnapshotStore(
                self.guard, self.base / "small-manifest", max_manifest_bytes=1
            ).save(self.snapshot)

    def test_ensure_blob_race_does_not_leak_snapshot_missing(self) -> None:
        original_read = artifact_module.read_guarded_file
        blob = self.blob_path

        def disappear_then_read(*args: object, **kwargs: object) -> bytes:
            blob.unlink()
            return original_read(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(
            artifact_module, "read_guarded_file", side_effect=disappear_then_read
        ):
            with self.assertRaises(WorkspaceError) as captured:
                self.store.save(self.snapshot)

        self.assertNotIsInstance(captured.exception, SnapshotMissingError)


if __name__ == "__main__":
    unittest.main()
