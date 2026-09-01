from __future__ import annotations

from ._checkpoint_fork import CheckpointForkRepositoryMixin
from ._database import SessionDatabase
from ._peer_messages import PeerMessageRepositoryMixin
from ._peer_sessions import PeerSessionRepositoryMixin
from ._records import RecordRepositoryMixin
from ._rewinds import RewindRepositoryMixin
from ._semantic import SemanticRepositoryMixin
from ._session_rewind import AtomicSessionRewindRepositoryMixin
from ._skills import SkillActivationRepositoryMixin
from ._task_records import TaskRecordRepositoryMixin
from ._task_runtime_records import TaskRuntimeRepositoryMixin
from ._thread_content import ThreadContentRepositoryMixin
from ._workflows import WorkflowRepositoryMixin
from ._workspace_snapshots import WorkspaceSnapshotRepositoryMixin


class SQLiteSessionRepository(
    AtomicSessionRewindRepositoryMixin,
    CheckpointForkRepositoryMixin,
    RewindRepositoryMixin,
    WorkspaceSnapshotRepositoryMixin,
    TaskRuntimeRepositoryMixin,
    TaskRecordRepositoryMixin,
    PeerMessageRepositoryMixin,
    PeerSessionRepositoryMixin,
    ThreadContentRepositoryMixin,
    SkillActivationRepositoryMixin,
    WorkflowRepositoryMixin,
    SemanticRepositoryMixin,
    RecordRepositoryMixin,
):
    """Persist core sessions with one SQLite transaction per async operation."""

    def __init__(self, database_path: str | object) -> None:
        self._database = SessionDatabase(database_path)  # type: ignore[arg-type]
