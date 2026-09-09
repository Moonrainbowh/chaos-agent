from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from code_agent.sessions.rewind_models import RewindBaseline
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore

from code_agent_win.edit_plan_preview import default_workspace_fingerprint
from code_agent_win.edit_plan_store import WorkspaceEditPlanStore
from code_agent_win.rewind_capture import RewindCaptureCoordinator
from code_agent_win.rewind_gate import WorkspaceMutationGate
from code_agent_win.rewind_sessions import CoordinatedSessionRepository


@dataclass(frozen=True)
class WorkspaceMutationBundle:
    """Root-bound writers and durable safety services shared by dispatches."""

    editor: WorkspaceEditor
    capture: object | None
    gate: object | None
    snapshots: object | None
    coordinated: CoordinatedSessionRepository | None
    edit_plans: WorkspaceEditPlanStore
    workspace_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.editor, WorkspaceEditor):
            raise TypeError("editor must be a WorkspaceEditor")
        if not isinstance(self.edit_plans, WorkspaceEditPlanStore):
            raise TypeError("edit_plans must be a WorkspaceEditPlanStore")
        if (
            not isinstance(self.workspace_fingerprint, str)
            or len(self.workspace_fingerprint) != 64
        ):
            raise ValueError("workspace_fingerprint must be a SHA-256")


class WorkspaceMutationPool:
    """Materialize exactly one mutation bundle for each workspace root."""

    def __init__(self, source_services: object, source_capture: object | None) -> None:
        self._source_capture = source_capture
        self._lock = threading.RLock()
        source = self._source_bundle(source_services, source_capture)
        self._bundles = {_root_key(source_services): source}

    def for_services(self, services: object) -> WorkspaceMutationBundle:
        key = _root_key(services)
        with self._lock:
            current = self._bundles.get(key)
            if current is not None:
                return current
            created = self._task_bundle(services)
            self._bundles[key] = created
            return created

    def coordinated_for_services(
        self, services: object
    ) -> CoordinatedSessionRepository:
        coordinated = self.for_services(services).coordinated
        if coordinated is None:
            raise RuntimeError("workspace session coordination is unavailable")
        return coordinated

    async def needs_edit_batch_recovery(self, root: Path) -> bool:
        """Check under the existing workspace gate before building Git/UI services.

        This is only a negative fast path. A positive result goes through the
        normal recovery path, which reacquires the gate and rereads durable facts.
        No result is cached across startup calls or later mutations.
        """
        source = self._source_capture
        if source is None:
            return False
        if not isinstance(source, RewindCaptureCoordinator):
            return True
        key = str(root.resolve()).casefold()
        with self._lock:
            current = self._bundles.get(key)
        if current is not None:
            gate, fingerprint = current.gate, current.workspace_fingerprint
        else:
            editor = WorkspaceEditor(WorkspacePathGuard(root))
            fingerprint = default_workspace_fingerprint(editor)
            gate = WorkspaceMutationGate(
                _gate_state_root(source.gate, source.workspace_fingerprint),
                fingerprint,
            )
        if not isinstance(gate, WorkspaceMutationGate):
            raise RuntimeError("workspace recovery gate is unavailable")
        lease = await gate.acquire()
        try:
            records = await source.sessions.list_unresolved_edit_batches(fingerprint)
            if type(records) is not tuple:
                raise TypeError("workspace recovery query returned invalid results")
            return bool(records)
        finally:
            await lease.release()

    def _source_bundle(
        self, services: object, capture: object | None
    ) -> WorkspaceMutationBundle:
        guard = _guard(services)
        if capture is None:
            editor = WorkspaceEditor(guard)
            return WorkspaceMutationBundle(
                editor,
                None,
                None,
                None,
                None,
                WorkspaceEditPlanStore(),
                default_workspace_fingerprint(editor),
            )
        editor = getattr(capture, "editor", None)
        if not isinstance(editor, WorkspaceEditor):
            raise TypeError("source capture must expose a WorkspaceEditor")
        _require_editor_root(editor, _root(services))
        fingerprint = getattr(capture, "workspace_fingerprint", None)
        snapshots = getattr(capture, "snapshots", None)
        gate = getattr(capture, "gate", None)
        coordinated = _coordinated(capture, gate, fingerprint)
        return WorkspaceMutationBundle(
            editor,
            capture,
            gate,
            snapshots,
            coordinated,
            WorkspaceEditPlanStore(),
            fingerprint,
        )

    def _task_bundle(self, services: object) -> WorkspaceMutationBundle:
        editor = WorkspaceEditor(_guard(services))
        source = self._source_capture
        if source is None:
            return WorkspaceMutationBundle(
                editor,
                None,
                None,
                None,
                None,
                WorkspaceEditPlanStore(),
                default_workspace_fingerprint(editor),
            )
        if not isinstance(source, RewindCaptureCoordinator):
            raise RuntimeError("task workspace capture cannot be derived safely")
        source_snapshots = source.snapshots
        source_gate = source.gate
        if not isinstance(source_snapshots, WorkspaceSnapshotStore):
            raise RuntimeError("source snapshot store is unavailable")
        if not isinstance(source_gate, WorkspaceMutationGate):
            raise RuntimeError("source mutation gate is unavailable")
        snapshots = WorkspaceSnapshotStore(
            _guard(services), source_snapshots.root
        )
        gate = WorkspaceMutationGate(
            _gate_state_root(source_gate, source.workspace_fingerprint),
            snapshots.workspace_fingerprint,
        )
        baseline = (
            RewindBaseline.UNKNOWN
            if getattr(services, "git", None) is not None
            else RewindBaseline.NON_GIT_EXISTING
        )
        capture = RewindCaptureCoordinator(
            source.sessions,
            editor,
            snapshots,
            gate,
            existing_baseline=baseline,
        )
        return WorkspaceMutationBundle(
            editor,
            capture,
            gate,
            snapshots,
            CoordinatedSessionRepository(
                source.sessions, gate, snapshots.workspace_fingerprint
            ),
            WorkspaceEditPlanStore(),
            snapshots.workspace_fingerprint,
        )


def _gate_state_root(gate: WorkspaceMutationGate, fingerprint: str) -> Path:
    path = gate.path
    if (
        path.name != "mutation-gate.sqlite3"
        or path.parent.name != fingerprint
        or path.parent.parent.name != "rewind"
    ):
        raise RuntimeError("source mutation gate layout is invalid")
    return path.parent.parent.parent


def _coordinated(
    capture: object, gate: object, fingerprint: object
) -> CoordinatedSessionRepository | None:
    if not isinstance(capture, RewindCaptureCoordinator):
        return None
    if not isinstance(gate, WorkspaceMutationGate) or not isinstance(
        fingerprint, str
    ):
        raise RuntimeError("source coordination services are unavailable")
    return CoordinatedSessionRepository(capture.sessions, gate, fingerprint)


def _root_key(services: object) -> str:
    return str(_root(services)).casefold()


def _root(services: object) -> Path:
    root = getattr(services, "root", None)
    if not isinstance(root, Path):
        raise TypeError("workspace services must expose a Path root")
    return root.resolve()


def _guard(services: object):
    guard = getattr(services, "guard", None)
    if guard is None:
        raise TypeError("workspace services must expose a guard")
    return guard


def _require_editor_root(editor: WorkspaceEditor, expected: Path) -> None:
    if editor.guard.root != expected:
        raise RuntimeError("source capture belongs to another workspace")


__all__ = ["WorkspaceMutationBundle", "WorkspaceMutationPool"]
