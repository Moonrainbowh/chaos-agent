from __future__ import annotations

import os
from pathlib import Path

from code_agent.config.loader import load_runtime_config
from code_agent.sessions.legacy_migration import migrate_legacy_session_database

from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.app_factory import create_application as _create_application
from code_agent_win.app_models import Application
from code_agent_win.context_runtime import build_context_runtime
from code_agent_win.runtime_support import model_client


_model_client = model_client


def create_application(
    workspace_root: Path | None = None,
    *,
    model_name: str | None = None,
    profile_name: str | None = None,
    mode_name: str | None = None,
) -> Application:
    return _create_application(
        workspace_root,
        model_name=model_name,
        profile_name=profile_name,
        mode_name=mode_name,
        load_config=load_runtime_config,
        model_factory=_model_client,
        session_path_factory=_session_path,
        context_factory=build_context_runtime,
    )


def _session_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "chaos-agent"
    legacy = Path(base) / "code-agent" / "sessions.sqlite3"
    directory.mkdir(parents=True, exist_ok=True)
    current = directory / "sessions.sqlite3"
    if not current.exists() and legacy.exists():
        migrate_legacy_session_database(legacy, current)
    return current


__all__ = [
    "Application",
    "RootActionDispatcher",
    "create_application",
]
