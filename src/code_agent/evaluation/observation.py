from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .trace import TrustedExecutionTrace


def content_digest(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(b"text\0" + normalized.encode("utf-8")).hexdigest()


def workspace_manifest(root: Path) -> dict[str, str]:
    """Hash regular files and record links without following them."""
    if not isinstance(root, Path) or not root.is_dir():
        raise ValueError("workspace root must be a directory")
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            manifest[relative] = f"link:{path.readlink()}"
        elif path.is_file():
            manifest[relative] = _file_digest(path.read_bytes())
    return manifest


def manifest_digest(manifest: Mapping[str, str]) -> str:
    serialized = json.dumps(dict(sorted(manifest.items())), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    paths = set(before) | set(after)
    return tuple(sorted(path for path in paths if before.get(path) != after.get(path)))


def _file_digest(content: bytes) -> str:
    try:
        return content_digest(content.decode("utf-8"))
    except UnicodeDecodeError:
        return hashlib.sha256(b"binary\0" + content).hexdigest()


@dataclass(frozen=True)
class TrustedVerifierResult:
    name: str
    baseline_exit_code: int | None
    final_exit_code: int | None
    baseline_timed_out: bool
    final_timed_out: bool
    workspace_digest: str
    baseline_infrastructure_failure: str | None = None
    final_infrastructure_failure: str | None = None

    @property
    def baseline_failed(self) -> bool:
        return (
            self.baseline_infrastructure_failure is None
            and not self.baseline_timed_out
            and self.baseline_exit_code is not None
            and self.baseline_exit_code != 0
        )

    @property
    def passed(self) -> bool:
        return (
            self.final_infrastructure_failure is None
            and not self.final_timed_out
            and self.final_exit_code == 0
        )

    @property
    def infrastructure_failures(self) -> tuple[str, ...]:
        return tuple(
            failure
            for failure in (
                self.baseline_infrastructure_failure,
                self.final_infrastructure_failure,
            )
            if failure is not None
        )


@dataclass(frozen=True)
class HarnessObservation:
    """Evidence produced by the runner, outside the model-visible result."""

    baseline_manifest: Mapping[str, str]
    final_manifest: Mapping[str, str]
    verifier_results: tuple[TrustedVerifierResult, ...]
    elapsed_seconds: float
    timed_out: bool
    workspace_path: Path
    workspace_removed: bool
    trace: TrustedExecutionTrace = TrustedExecutionTrace()
    outside_workspace_paths: tuple[str, ...] = ()
    baseline_clones_removed_before_execution: bool = False
    final_snapshot_frozen: bool = False
    isolation_mode: str = "in_process"
    termination_confirmed: bool = True
    infrastructure_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_manifest", dict(self.baseline_manifest))
        object.__setattr__(self, "final_manifest", dict(self.final_manifest))
        verifiers = tuple(self.verifier_results)
        if not all(isinstance(item, TrustedVerifierResult) for item in verifiers):
            raise TypeError("verifier results must be trusted verifier results")
        object.__setattr__(self, "verifier_results", verifiers)
        object.__setattr__(self, "outside_workspace_paths", tuple(self.outside_workspace_paths))
        object.__setattr__(self, "infrastructure_failures", tuple(self.infrastructure_failures))
        if not isinstance(self.trace, TrustedExecutionTrace):
            raise TypeError("trace must be a trusted execution trace")
        if not isinstance(self.workspace_path, Path):
            raise TypeError("workspace_path must be a Path")
        if isinstance(self.elapsed_seconds, bool) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        for name in (
            "timed_out",
            "workspace_removed",
            "baseline_clones_removed_before_execution",
            "final_snapshot_frozen",
            "termination_confirmed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return changed_paths(self.baseline_manifest, self.final_manifest)

    @property
    def final_digest(self) -> str:
        return manifest_digest(self.final_manifest)

    @property
    def evidence_generation(self) -> int:
        return int(bool(self.changed_paths))
