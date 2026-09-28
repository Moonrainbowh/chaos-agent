from __future__ import annotations

from pathlib import Path

from code_agent.workspace.windows_paths import require_supported_windows_path


_DIGEST_SENTINEL = "0" * 64


def resolve_managed_storage_root(value: Path) -> Path:
    literal = value.expanduser()
    _require_managed_storage_paths(literal)
    canonical = literal.resolve(strict=False)
    _require_managed_storage_paths(canonical)
    return canonical


def _require_managed_storage_paths(root: Path) -> None:
    require_supported_windows_path(root, operation="managed workspace storage")
    require_supported_windows_path(
        root / "worktrees", operation="managed workspace worktrees"
    )
    require_supported_windows_path(
        root / "snapshots" / "blobs" / "00" / _DIGEST_SENTINEL,
        operation="managed workspace snapshots",
    )


__all__ = ["resolve_managed_storage_root"]
