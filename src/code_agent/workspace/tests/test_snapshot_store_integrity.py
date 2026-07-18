from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.errors import SnapshotIntegrityError, WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
import code_agent.workspace._atomic_artifact_write as writer_module  # noqa: E402
from code_agent.workspace.snapshot_store import (  # noqa: E402
    SnapshotHandle,
    WorkspaceSnapshotStore,
)


def canonical(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class SnapshotIntegrityTests(unittest.TestCase):
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

    def manifest(self) -> dict[str, object]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def rewrite_manifest(self, payload: object, *, canonical_json: bool = True) -> SnapshotHandle:
        raw = canonical(payload) if canonical_json else json.dumps(payload, indent=2).encode("utf-8")
        self.manifest_path.write_bytes(raw)
        return replace(self.handle, digest=hashlib.sha256(raw).hexdigest())

    def test_tampered_and_noncanonical_manifests_fail_closed(self) -> None:
        original = self.manifest_path.read_bytes()
        self.manifest_path.write_bytes(original + b"\n")
        with self.assertRaises(WorkspaceError):
            self.store.load(self.handle)

        payload = json.loads(original.decode("utf-8"))
        noncanonical = self.rewrite_manifest(payload, canonical_json=False)
        with self.assertRaises(WorkspaceError):
            self.store.load(noncanonical)

    def test_tampered_blob_size_and_hash_fail_closed(self) -> None:
        entry = self.manifest()["entries"][0]  # type: ignore[index]
        blob = self.artifacts / "blobs" / entry["digest"]  # type: ignore[index]
        original = blob.read_bytes()
        for content in (b"x" * len(original), original + b"!"):
            with self.subTest(size=len(content)):
                blob.write_bytes(content)
                with self.assertRaises(WorkspaceError):
                    self.store.load(self.handle)
                blob.write_bytes(original)

    def test_handle_digest_paths_and_total_must_match_manifest(self) -> None:
        handles = (
            replace(self.handle, digest="0" * 64),
            replace(self.handle, paths=("other.bin",)),
            replace(self.handle, total_bytes=self.handle.total_bytes + 1),
        )
        for handle in handles:
            with self.subTest(handle=handle):
                with self.assertRaises(WorkspaceError):
                    self.store.load(handle)

    def test_manifest_fields_and_types_are_strict(self) -> None:
        original = self.manifest()
        mutations = []
        extra = copy.deepcopy(original)
        extra["extra"] = True
        mutations.append(extra)
        wrong_total = copy.deepcopy(original)
        wrong_total["total_bytes"] = True
        mutations.append(wrong_total)
        wrong_entry = copy.deepcopy(original)
        wrong_entry["entries"][0]["digest"] = "not-a-digest"  # type: ignore[index]
        mutations.append(wrong_entry)
        extra_entry = copy.deepcopy(original)
        extra_entry["entries"][0]["extra"] = True  # type: ignore[index]
        mutations.append(extra_entry)

        for payload in mutations:
            with self.subTest(payload=payload):
                handle = self.rewrite_manifest(payload)
                with self.assertRaises(WorkspaceError):
                    self.store.load(handle)

    def test_manifest_paths_reject_traversal_sensitive_and_absolute_values(self) -> None:
        for path in ("../outside.bin", ".env", str(self.root / "file.bin")):
            with self.subTest(path=path):
                payload = self.manifest()
                payload["entries"][0]["path"] = path  # type: ignore[index]
                handle = self.rewrite_manifest(payload)
                with self.assertRaises(WorkspaceError):
                    self.store.load(handle)

    def test_manifest_link_path_fails_closed(self) -> None:
        link = self.root / "linked"
        link.mkdir()
        payload = self.manifest()
        payload["entries"][0]["path"] = "linked/secret.bin"  # type: ignore[index]
        handle = self.rewrite_manifest(payload)

        with patch(
            "code_agent.workspace.paths._is_link_like",
            side_effect=lambda path: path == link,
        ):
            with self.assertRaises(WorkspaceError):
                self.store.load(handle)

    def test_workspace_fingerprint_prevents_cross_workspace_reuse(self) -> None:
        other_root = self.base / "other-workspace"
        other_root.mkdir()
        other = WorkspaceSnapshotStore(WorkspacePathGuard(other_root), self.artifacts)

        with self.assertRaises(WorkspaceError):
            other.load(self.handle)

    def test_manifest_read_is_bounded(self) -> None:
        limit = self.manifest_path.stat().st_size - 1
        bounded = WorkspaceSnapshotStore(
            self.guard, self.artifacts, max_manifest_bytes=limit
        )

        with self.assertRaises(SnapshotIntegrityError):
            bounded.load(self.handle)

    def test_atomic_manifest_failure_cleans_same_directory_temporary_file(self) -> None:
        before = {path.name for path in (self.artifacts / "manifests").iterdir()}
        real_replace = os.replace

        def fail_manifest(source: object, target: object) -> None:
            if Path(target).parent == self.artifacts / "manifests":
                raise OSError("busy")
            real_replace(source, target)

        if os.name == "nt":
            failure = patch(
                "code_agent.workspace._windows_artifact_write.rename_relative",
                side_effect=OSError("busy"),
            )
        else:
            failure = patch.object(writer_module.os, "replace", side_effect=fail_manifest)

        with failure:
            with self.assertRaises(WorkspaceError):
                self.store.save(self.snapshot)

        after = {path.name for path in (self.artifacts / "manifests").iterdir()}
        self.assertEqual(after, before)
        self.assertFalse(any(name.startswith(".snapshot-") for name in after))

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing semantics only")
    def test_atomic_write_stays_on_verified_directory_during_swap(self) -> None:
        manifests = self.artifacts / "manifests"
        moved = self.artifacts / "moved-manifests"
        renamed: list[bool] = []

        def race_directory(parent_handle: int, parent: Path) -> None:
            del parent_handle
            if parent != manifests:
                return
            try:
                manifests.rename(moved)
            except OSError:
                renamed.append(False)
            else:
                renamed.append(True)
                manifests.mkdir()

        with patch(
            "code_agent.workspace._windows_artifact_write._before_relative_write",
            side_effect=race_directory,
        ):
            handle = self.store.save(self.snapshot)

        self.assertEqual(len(renamed), 1)
        manifest_name = f"{handle.identifier}.json"
        expected = moved if renamed[0] else manifests
        replacement = manifests if renamed[0] else moved
        self.assertTrue((expected / manifest_name).is_file())
        self.assertFalse((replacement / manifest_name).exists())


if __name__ == "__main__":
    unittest.main()
