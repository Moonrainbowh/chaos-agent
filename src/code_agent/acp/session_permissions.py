from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

import acp.schema as schema
from acp import RequestError

from code_agent.policy.models import ApprovalMode


_DEFAULT_MODE = "auto"
_MODES = {
    "auto": ApprovalMode.AUTO,
    "session-all": ApprovalMode.UNRESTRICTED,
}


class SessionPermissions:
    def __init__(
        self,
        permission_scope: Callable[
            [ApprovalMode, str], AbstractContextManager[None]
        ]
        | None,
    ) -> None:
        if permission_scope is not None and not callable(permission_scope):
            raise TypeError("permission_scope must be callable or None")
        self._permission_scope = permission_scope
        self._modes: dict[str, str] = {}

    def reset(self) -> None:
        self._modes.clear()

    def open(self, session_id: str) -> None:
        self._modes[session_id] = _DEFAULT_MODE

    def ensure(self, session_id: str) -> None:
        self._modes.setdefault(session_id, _DEFAULT_MODE)

    def close(self, session_id: str) -> None:
        self._modes.pop(session_id, None)

    def set(self, session_id: str, mode_id: str) -> None:
        if mode_id not in _MODES:
            raise RequestError.invalid_params(
                {"modeId": "must be auto or session-all"}
            )
        self._modes[session_id] = mode_id

    def scope(self, session_id: str) -> AbstractContextManager[None]:
        mode_id = self._modes.setdefault(session_id, _DEFAULT_MODE)
        if self._permission_scope is None:
            return nullcontext()
        return self._permission_scope(
            _MODES[mode_id], f"acp_{mode_id.replace('-', '_')}"
        )

    def state(self, session_id: str) -> schema.SessionModeState:
        return schema.SessionModeState(
            currentModeId=self._modes.get(session_id, _DEFAULT_MODE),
            availableModes=[
                schema.SessionMode(
                    id="auto",
                    name="Auto",
                    description=(
                        "Automatically allow recognized current-workspace actions, "
                        "including local PowerShell."
                    ),
                ),
                schema.SessionMode(
                    id="session-all",
                    name="Allow all this session",
                    description=(
                        "Allow recognized non-critical actions for this ACP session; "
                        "unknown, critical, and protected actions remain blocked."
                    ),
                ),
            ],
        )


__all__ = ["SessionPermissions"]
