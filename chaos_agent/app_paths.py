from __future__ import annotations

import os
from pathlib import Path

from code_agent.sessions.legacy_migration import migrate_legacy_session_database
from code_agent.workspace.windows_paths import require_supported_windows_path
from code_agent_win.windows_storage_paths import resolve_managed_storage_root


_DIGEST_SENTINEL = "0" * 64


def product_state_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    literal = Path(base).expanduser() / "chaos-agent"
    _require_product_state_paths(literal)
    canonical = literal.resolve(strict=False)
    _require_product_state_paths(canonical)
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def session_path() -> Path:
    directory = product_state_root()
    legacy = directory.parent / "code-agent" / "sessions.sqlite3"
    current = directory / "sessions.sqlite3"
    if not current.exists() and legacy.exists():
        migrate_legacy_session_database(legacy, current)
    return current


def workspace_storage_path() -> Path:
    override = os.getenv("CHAOS_WORKSPACE_STORAGE") or os.getenv("CODE_AGENT_WORKSPACE_STORAGE")
    if override:
        literal = Path(override).expanduser()
        return resolve_managed_storage_root(literal)
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    # Keep managed worktrees outside the protected API configuration directory.
    return resolve_managed_storage_root(
        Path(base).expanduser() / "chaos-agent-workspaces"
    )


def _require_product_state_paths(root: Path) -> None:
    paths = (
        root,
        root / "sessions.sqlite3.migrating-journal",
        root / "attachments" / "00" / f"{_DIGEST_SENTINEL}.blob",
        root / "rewind-snapshots" / "blobs" / _DIGEST_SENTINEL,
        root / "rewind" / _DIGEST_SENTINEL / "mutation-gate.sqlite3-journal",
        root / "managed-workspaces" / "snapshots" / "blobs" / "00" / _DIGEST_SENTINEL,
    )
    for path in paths:
        require_supported_windows_path(
            path,
            operation="application product state",
        )
