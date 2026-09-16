from __future__ import annotations

from ._checkpoint_fork import CheckpointForkRepositoryMixin
from ._context_journal import ContextJournalRepositoryMixin
from ._context_notes import ContextNotesRepositoryMixin
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
from ._memory import MemoryRepositoryMixin
from ._recovery import RecoveryRepositoryMixin


class SQLiteSessionRepository(
    ContextNotesRepositoryMixin,
    ContextJournalRepositoryMixin,
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
    MemoryRepositoryMixin,
    RecoveryRepositoryMixin,
):
    """Persist core sessions with one SQLite transaction per async operation."""

    def __init__(self, database_path: str | object) -> None:
        self._database = SessionDatabase(database_path)  # type: ignore[arg-type]

    def close(self) -> None:
        """Release repository resources; operations use short-lived connections."""
        close = getattr(self._database, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "SQLiteSessionRepository":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
