from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .errors import SessionMigrationError


@dataclass(frozen=True)
class LegacyMigrationResult:
    migrated: bool
    thread_count: int
    task_count: int


def migrate_legacy_session_database(source: Path, destination: Path) -> LegacyMigrationResult:
    """Copy a legacy SQLite DB atomically without deleting or moving its source."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        return LegacyMigrationResult(False, *_counts(destination))
    if not source.is_file():
        raise SessionMigrationError("legacy session database is unavailable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".migrating")
    try:
        if temporary.exists():
            temporary.unlink()
        old = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        new = sqlite3.connect(temporary)
        try:
            expected = _counts_connection(old)
            old.backup(new)
            new.commit()
        finally:
            new.close()
            old.close()
        actual = _counts(temporary)
        if actual != expected or _integrity(temporary) != "ok":
            raise SessionMigrationError("legacy session database integrity check failed")
        os.replace(temporary, destination)
        return LegacyMigrationResult(True, *actual)
    except (OSError, sqlite3.Error) as error:
        raise SessionMigrationError(f"legacy session migration failed: {type(error).__name__}") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def _counts(path: Path) -> tuple[int, int]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        return _counts_connection(connection)


def _counts_connection(connection: sqlite3.Connection) -> tuple[int, int]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return tuple(int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) if name in tables else 0 for name in ("threads", "tasks"))  # type: ignore[return-value]


def _integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
