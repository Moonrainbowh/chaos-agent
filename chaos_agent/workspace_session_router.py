from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from code_agent.core._json import JSONValue, validate_json_mapping
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent_win.rewind_sessions import CoordinatedSessionRepository


RootForThread = Callable[[str], Path | None]
CoordinatedForRoot = Callable[[Path], CoordinatedSessionRepository]


class WorkspaceSessionRouter:
    """Route anchored checkpoints while forwarding one shared repository."""

    def __init__(
        self,
        base: RewindSessionRepository,
        default_root: Path,
        root_for_thread: RootForThread,
        coordinated_for_root: CoordinatedForRoot,
        *,
        fixed_owner_thread_id: str | None = None,
    ) -> None:
        if not isinstance(base, RewindSessionRepository):
            raise TypeError("base must be a RewindSessionRepository")
        if not isinstance(default_root, Path):
            raise TypeError("default_root must be a Path")
        if not callable(root_for_thread):
            raise TypeError("root_for_thread must be callable")
        if not callable(coordinated_for_root):
            raise TypeError("coordinated_for_root must be callable")
        self._base = base
        self._default_root = default_root.resolve()
        self._root_for_thread = root_for_thread
        self._coordinated_for_root = coordinated_for_root
        self._fixed_owner_thread_id = _identifier(
            fixed_owner_thread_id,
            "owner_thread_id",
            optional=True,
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._base, name)

    def for_owner(self, owner_thread_id: str) -> "WorkspaceSessionRouter":
        owner = _identifier(owner_thread_id, "owner_thread_id")
        return WorkspaceSessionRouter(
            self._base,
            self._default_root,
            self._root_for_thread,
            self._coordinated_for_root,
            fixed_owner_thread_id=owner,
        )

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> str:
        thread = _identifier(thread_id, "thread_id")
        checkpoint_label = _identifier(label, "label")
        data: Mapping[str, JSONValue] = {} if metadata is None else metadata
        validate_json_mapping(data, "metadata")
        coordinated = self._coordinated(self._root(thread))
        owner = self._fixed_owner_thread_id
        if owner is not None:
            coordinated = self._validate_coordinated(
                coordinated.for_owner(owner)
            )
        return await coordinated.create_checkpoint(
            thread, checkpoint_label, data
        )

    def _root(self, thread_id: str) -> Path:
        root = self._lookup_root(thread_id)
        if root is None and self._fixed_owner_thread_id is not None:
            root = self._lookup_root(self._fixed_owner_thread_id)
        return self._default_root if root is None else root

    def _lookup_root(self, thread_id: str) -> Path | None:
        root = self._root_for_thread(thread_id)
        if root is None:
            return None
        if not isinstance(root, Path):
            raise TypeError("root_for_thread must return a Path or None")
        return root.resolve()

    def _coordinated(self, root: Path) -> CoordinatedSessionRepository:
        return self._validate_coordinated(self._coordinated_for_root(root))

    def _validate_coordinated(
        self, value: object
    ) -> CoordinatedSessionRepository:
        if not isinstance(value, CoordinatedSessionRepository):
            raise TypeError(
                "coordinated_for_root must return a "
                "CoordinatedSessionRepository"
            )
        if getattr(value, "_base", None) is not self._base:
            raise RuntimeError(
                "coordinated repository must share the router base"
            )
        return value


def _identifier(
    value: object,
    name: str,
    *,
    optional: bool = False,
) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


__all__ = ["WorkspaceSessionRouter"]
