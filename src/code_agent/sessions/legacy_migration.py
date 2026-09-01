from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from code_agent._owned_temporary import (
    OwnedTemporary,
    OwnedTemporaryReplacedError,
)

from ._logical_database import logical_database_digest
from .errors import SessionMigrationError


@dataclass(frozen=True)
class LegacyMigrationResult:
    migrated: bool
    thread_count: int
    task_count: int
    cleanup_warning: str | None = None


@dataclass(frozen=True)
class _Publication:
    published: bool
    temporary_consumed: bool
    cleanup_warning: str | None = None


@dataclass(frozen=True)
class _DatabaseSnapshot:
    counts: tuple[int, int]
    logical_digest: str


def migrate_legacy_session_database(
    source: Path,
    destination: Path,
) -> LegacyMigrationResult:
    """Copy a legacy SQLite DB atomically without deleting or moving its source."""
    source, destination = Path(source), Path(destination)
    temporary: OwnedTemporary | None = None
    outcome: LegacyMigrationResult | None = None
    failure: BaseException | None = None
    try:
        if destination.exists():
            existing = _validated_snapshot(destination, "existing destination")
            return LegacyMigrationResult(False, *existing.counts)
        if not source.is_file():
            raise SessionMigrationError("legacy session database is unavailable")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = _create_temporary(destination)
        with closing(
            sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        ) as old, closing(sqlite3.connect(temporary.path)) as new:
            expected = _validated_snapshot_connection(old, "legacy source")
            old.backup(new)
            new.commit()

        actual = _validated_snapshot(
            temporary.path,
            "migration temporary database",
        )
        if actual != expected:
            raise SessionMigrationError(
                "legacy session migration logical content validation failed"
            )

        publication = _publish_without_overwrite(
            temporary,
            destination,
            expected,
        )
        if not publication.published:
            concurrent = _validated_snapshot(destination, "concurrent destination")
            if concurrent != expected:
                raise SessionMigrationError(
                    "concurrent legacy session destination content differs "
                    "from the legacy source snapshot"
                )
            outcome = LegacyMigrationResult(False, *concurrent.counts)
        else:
            outcome = LegacyMigrationResult(
                True,
                *actual.counts,
                getattr(publication, "cleanup_warning", None),
            )
        if publication.temporary_consumed:
            # Rename consumed our unique name; never unlink a later occupant.
            temporary = None
    except SessionMigrationError as error:
        failure = error
        raise
    except (OSError, sqlite3.Error) as error:
        wrapped = SessionMigrationError(
            f"legacy session migration failed: {_error_summary(error)}"
        )
        failure = wrapped
        raise wrapped from None
    except BaseException as error:
        failure = error
        raise
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError as error:
                summary = (
                    str(error)
                    if isinstance(error, OwnedTemporaryReplacedError)
                    else _error_summary(error)
                )
                detail = (
                    "owned migration temporary cleanup failed: "
                    f"{summary}"
                )
                if failure is None:
                    if outcome is None:
                        raise SessionMigrationError(detail) from None
                    outcome = replace(outcome, cleanup_warning=detail)
                else:
                    add_note = getattr(failure, "add_note", None)
                    if callable(add_note):
                        add_note(detail)
    if outcome is None:
        raise SessionMigrationError("legacy session migration produced no result")
    return outcome


def _create_temporary(destination: Path) -> OwnedTemporary:
    descriptor, name = tempfile.mkstemp(
        prefix=".mig-",
        dir=destination.parent,
    )
    cleanup: OwnedTemporary | None = None
    temporary: OwnedTemporary | None = None
    failure: BaseException | None = None
    try:
        if os.name == "nt":
            cleanup = OwnedTemporary.capture_cleanup_descriptor(
                Path(name), descriptor
            )
        temporary = OwnedTemporary.capture_descriptor(Path(name), descriptor)
    except BaseException as error:
        failure = error
    try:
        os.close(descriptor)
    except BaseException as error:
        failure = failure or error
    if failure is not None:
        if cleanup is not None:
            try:
                cleanup.cleanup()
            except OSError as cleanup_error:
                add_note = getattr(failure, "add_note", None)
                if callable(add_note):
                    add_note(f"migration temporary cleanup failed: {cleanup_error}")
        raise failure
    assert temporary is not None
    return temporary


def _publish_without_overwrite(
    temporary: OwnedTemporary,
    destination: Path,
    expected: _DatabaseSnapshot,
) -> _Publication:
    def verify(path: Path) -> None:
        actual = _validated_snapshot(path, "migration publication")
        if actual != expected:
            raise SessionMigrationError(
                "legacy session publication content differs from source snapshot"
            )

    try:
        consumed = temporary.publish_no_replace(destination, verify)
    except OSError as error:
        if getattr(error, "publication_committed", False):
            verify(destination)
            return _Publication(
                True,
                True,
                "migration publication committed but handle finalization "
                f"failed: {_error_summary(error)}",
            )
        if destination.exists():
            return _Publication(False, False)
        raise
    return _Publication(True, consumed)


def _validated_snapshot(path: Path, label: str) -> _DatabaseSnapshot:
    try:
        with closing(
            sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            return _validated_snapshot_connection(connection, label)
    except SessionMigrationError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise SessionMigrationError(
            f"{label} validation failed: {_error_summary(error)}"
        ) from None


def _validated_snapshot_connection(
    connection: sqlite3.Connection,
    label: str,
) -> _DatabaseSnapshot:
    if not connection.in_transaction:
        connection.execute("BEGIN")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]) != "ok":
        raise SessionMigrationError(f"{label} integrity check failed")
    return _DatabaseSnapshot(
        _counts_connection(connection),
        logical_database_digest(connection),
    )


def _counts_connection(connection: sqlite3.Connection) -> tuple[int, int]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    return tuple(
        int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        if name in tables
        else 0
        for name in ("threads", "tasks")
    )  # type: ignore[return-value]


def _error_summary(error: OSError | sqlite3.Error) -> str:
    code = getattr(error, "winerror", None)
    if code is None:
        code = getattr(error, "sqlite_errorname", None)
    suffix = f" ({code})" if code is not None else ""
    return f"{type(error).__name__}{suffix}"
