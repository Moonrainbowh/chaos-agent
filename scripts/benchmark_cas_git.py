"""Measure Git path discovery and CAS snapshot work on a synthetic repository.

This is intentionally an external benchmark: it does not change production
snapshot or Git behavior.  The output is JSON so S3 reports can be reproduced
and compared after implementation changes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from statistics import median

from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.inventory import WorkspaceInventory
from code_agent.workspace.snapshot_store import ContentAddressedSnapshotStore


def timed(function):
    started = time.perf_counter()
    value = function()
    return value, (time.perf_counter() - started) * 1000


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def create_repository(
    root: Path, tracked: int, changed: int, untracked: int, ignored: int, deleted: int
) -> None:
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    for index in range(tracked):
        content = (f"shared-{index % 32}\n" * 16).encode()
        path = root / "src" / f"file-{index:05d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git(root, "init", "-q")
    git(root, "config", "user.email", "benchmark@example.invalid")
    git(root, "config", "user.name", "CAS benchmark")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")

    for index in range(changed):
        path = root / "src" / f"file-{index:05d}.txt"
        path.write_bytes((f"changed-{index}\n" * 16).encode())
    for index in range(untracked):
        path = root / "new" / f"file-{index:05d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((f"untracked-{index}\n" * 8).encode())
    for index in range(deleted):
        (root / "src" / f"file-{tracked - index - 1:05d}.txt").unlink()
    for index in range(ignored):
        path = root / "ignored" / f"file-{index:05d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ignored\n" * 8)


def benchmark_once(args: argparse.Namespace) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="chaos-cas-git-") as temporary:
        root = Path(temporary).resolve()
        create_repository(
            root, args.tracked, args.changed, args.untracked, args.ignored, args.deleted
        )
        guard = WorkspacePathGuard(root)
        editor = WorkspaceEditor(guard)
        repository = GitWorkspace(root)

        all_paths, git_all_ms = timed(repository.snapshot_paths)
        changed_paths, git_changed_ms = timed(repository.changed_snapshot_paths)
        assert len(all_paths) == args.tracked + 1 + args.untracked
        assert len(changed_paths) == args.changed + args.untracked + args.deleted
        assert len(tuple((root / "ignored").glob("*.bin"))) == args.ignored
        assert all(not path.startswith("ignored/") for path in all_paths)
        inventory, inventory_ms = timed(
            lambda: WorkspaceInventory.capture(root, guard, repository)
        )
        def measure(paths: tuple[str, ...], name: str) -> dict[str, object]:
            snapshot, read_ms = timed(lambda: editor.snapshot(paths))
            modes = {
                entry.relative_path: (root / entry.relative_path).stat().st_mode & 0o777
                for entry in snapshot.entries
                if entry.existed
            }
            store = ContentAddressedSnapshotStore(root / f"cas-{name}")
            manifest, put_ms = timed(lambda: store.put(snapshot, modes))
            _, repeated_put_ms = timed(lambda: store.put(snapshot, modes))
            _, materialize_ms = timed(lambda: store.materialize(manifest))
            blob_files = tuple(
                path for path in (root / f"cas-{name}" / "blobs").rglob("*") if path.is_file()
            )
            blob_bytes = sum(path.stat().st_size for path in blob_files)
            input_bytes = sum(len(entry.content or b"") for entry in snapshot.entries)
            unique_digests = {
                entry.blob_sha256 for entry in manifest.entries if entry.existed
            }
            unique_bytes = sum(
                entry.size
                for entry in manifest.entries
                if entry.existed and entry.blob_sha256 in unique_digests
                and next(
                    candidate for candidate in manifest.entries
                    if candidate.blob_sha256 == entry.blob_sha256
                ) == entry
            )
            return {
                "entries": len(snapshot.entries),
                "existing_entries": sum(entry.existed for entry in snapshot.entries),
                "tombstones": sum(not entry.existed for entry in snapshot.entries),
                "input_bytes": input_bytes,
                "read_ms": round(read_ms, 3),
                "unique_blobs": len(blob_files),
                "unique_input_bytes": unique_bytes,
                "blob_bytes": blob_bytes,
                "put_ms": round(put_ms, 3),
                "repeated_put_ms": round(repeated_put_ms, 3),
                "materialize_ms": round(materialize_ms, 3),
            }

        changed = measure(changed_paths, "changed")
        full = measure(all_paths, "full")
        assert changed["tombstones"] == args.deleted
        assert full["tombstones"] == args.deleted
        assert changed["existing_entries"] == args.changed + args.untracked
        assert full["existing_entries"] == args.tracked + 1 - args.deleted + args.untracked
        assert len(inventory.entries) == args.tracked + 1 - args.deleted + args.untracked
        return {
            "parameters": vars(args),
            "fixture_accounting": {
                "tracked_payload_files": args.tracked,
                "tracked_gitignore_files": 1,
                "visible_untracked_files": args.untracked,
                "ignored_files_created": args.ignored,
                "deleted_tracked_files": args.deleted,
            },
            "git": {
                "all_paths": len(all_paths),
                "changed_paths": len(changed_paths),
                "snapshot_paths_ms": round(git_all_ms, 3),
                "changed_snapshot_paths_ms": round(git_changed_ms, 3),
            },
            "production_like_inventory": {
                "entries": len(inventory.entries),
                "bytes": sum(entry.size for entry in inventory.entries),
                "capture_ms": round(inventory_ms, 3),
            },
            "changed_snapshot": changed,
            "full_snapshot": full,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracked", type=int, default=1000)
    parser.add_argument("--changed", type=int, default=200)
    parser.add_argument("--untracked", type=int, default=50)
    parser.add_argument("--ignored", type=int, default=500)
    parser.add_argument("--deleted", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    runs = [benchmark_once(args) for _ in range(args.repeat)]
    def medians(path: tuple[str, ...]) -> dict[str, float]:
        values: dict[str, list[float]] = {}
        for run in runs:
            current: object = run
            for component in path:
                current = current[component]  # type: ignore[index]
            values.setdefault(".".join(path), []).append(float(current))
        return {key: round(median(samples), 3) for key, samples in values.items()}

    timing_paths = (
        ("git", "snapshot_paths_ms"),
        ("git", "changed_snapshot_paths_ms"),
        ("production_like_inventory", "capture_ms"),
        ("changed_snapshot", "read_ms"),
        ("changed_snapshot", "put_ms"),
        ("changed_snapshot", "repeated_put_ms"),
        ("changed_snapshot", "materialize_ms"),
        ("full_snapshot", "read_ms"),
        ("full_snapshot", "put_ms"),
        ("full_snapshot", "repeated_put_ms"),
        ("full_snapshot", "materialize_ms"),
    )
    print(
        json.dumps(
            {"repeat": args.repeat, "median_ms": {".".join(path): medians(path)[".".join(path)] for path in timing_paths}, "runs": runs},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
