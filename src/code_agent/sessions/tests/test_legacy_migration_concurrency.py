from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.errors import SessionMigrationError  # noqa: E402
from code_agent.sessions.legacy_migration import (  # noqa: E402
    LegacyMigrationResult,
    migrate_legacy_session_database,
)


class LegacyMigrationConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "legacy.sqlite3"
        self.destination = self.root / "current" / "sessions.sqlite3"
        _create_legacy_database(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_two_threads_publish_one_complete_destination(self) -> None:
        self.destination.parent.mkdir(parents=True)
        foreign_temporary = self.destination.with_suffix(
            self.destination.suffix + ".migrating"
        )
        foreign_temporary.write_bytes(b"belongs to another migration")
        source_uri = f"file:{self.source.as_posix()}?mode=ro"
        connect_barrier = threading.Barrier(2, timeout=5.0)
        real_connect = sqlite3.connect

        def synchronized_connect(database: object, *args: object, **kwargs: object):
            connection = real_connect(database, *args, **kwargs)
            if database == source_uri:
                connect_barrier.wait()
            return connection

        results: list[LegacyMigrationResult] = []
        errors: list[BaseException] = []

        def migrate() -> None:
            try:
                results.append(
                    migrate_legacy_session_database(self.source, self.destination)
                )
            except BaseException as error:
                # Worker exceptions otherwise disappear from unittest's main thread.
                errors.append(error)

        with patch(
            "code_agent.sessions.legacy_migration.sqlite3.connect",
            side_effect=synchronized_connect,
        ):
            workers = [threading.Thread(target=migrate) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result.migrated for result in results), [False, True])
        self.assertTrue(
            all(
                (result.thread_count, result.task_count) == (1, 1)
                for result in results
            )
        )
        self.assertEqual(_validated_counts(self.destination), (1, 1))
        self.assertEqual(_validated_counts(self.source), (1, 1))
        self.assertEqual(
            foreign_temporary.read_bytes(),
            b"belongs to another migration",
        )
        residue = list(self.destination.parent.glob(".mig-*"))
        self.assertEqual(residue, [])

    def test_corrupt_existing_destination_fails_closed(self) -> None:
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"not a sqlite database")

        with self.assertRaises(SessionMigrationError):
            migrate_legacy_session_database(self.source, self.destination)

        self.assertEqual(_validated_counts(self.source), (1, 1))

    def test_published_database_reports_owned_temp_cleanup_warning(self) -> None:
        def publish_as_link(temporary: object, destination: Path, _expected: object) -> object:
            destination.hardlink_to(getattr(temporary, "path"))
            return SimpleNamespace(published=True, temporary_consumed=False)

        def fail_owned_cleanup(temporary: object) -> None:
            raise PermissionError("migration temporary is locked")

        with patch(
            "code_agent.sessions.legacy_migration._publish_without_overwrite",
            side_effect=publish_as_link,
        ), patch(
            "code_agent.sessions.legacy_migration.OwnedTemporary.cleanup",
            autospec=True,
            side_effect=fail_owned_cleanup,
        ):
            result = migrate_legacy_session_database(self.source, self.destination)

        self.assertTrue(result.migrated)
        self.assertIn("temporary cleanup failed", result.cleanup_warning or "")
        self.assertEqual(_validated_counts(self.destination), (1, 1))

    def test_concurrent_winner_reports_owned_temp_cleanup_warning(self) -> None:
        def publish_concurrent_winner(
            temporary: object,
            destination: Path,
            expected: object,
        ) -> object:
            del temporary, expected
            shutil.copyfile(self.source, destination)
            return SimpleNamespace(published=False, temporary_consumed=False)

        def fail_owned_cleanup(temporary: object) -> None:
            raise PermissionError("migration temporary is locked")

        with patch(
            "code_agent.sessions.legacy_migration._publish_without_overwrite",
            side_effect=publish_concurrent_winner,
        ), patch(
            "code_agent.sessions.legacy_migration.OwnedTemporary.cleanup",
            autospec=True,
            side_effect=fail_owned_cleanup,
        ):
            result = migrate_legacy_session_database(self.source, self.destination)

        self.assertFalse(result.migrated)
        self.assertIn("temporary cleanup failed", result.cleanup_warning or "")
        self.assertEqual(_validated_counts(self.destination), (1, 1))

    def test_concurrent_same_counts_different_rows_fails_closed(self) -> None:
        def publish_unrelated_winner(
            temporary: object,
            destination: Path,
            expected: object,
        ) -> object:
            del temporary, expected
            _create_legacy_database(
                destination,
                thread_id="unrelated-thread",
                task_id="unrelated-task",
            )
            return SimpleNamespace(published=False, temporary_consumed=False)

        with patch(
            "code_agent.sessions.legacy_migration._publish_without_overwrite",
            side_effect=publish_unrelated_winner,
        ), self.assertRaisesRegex(SessionMigrationError, "content"):
            migrate_legacy_session_database(self.source, self.destination)

        self.assertEqual(_ids(self.source), ("legacy-thread", "legacy-task"))
        self.assertEqual(
            _ids(self.destination),
            ("unrelated-thread", "unrelated-task"),
        )

    def test_cleanup_preserves_replacement_of_owned_temporary(self) -> None:
        replacements: list[Path] = []

        def publish_then_replace_temporary(
            temporary: object,
            destination: Path,
            expected: object,
        ) -> object:
            del expected
            temporary = getattr(temporary, "path")
            temporary.unlink()
            temporary.write_bytes(b"belongs to another process")
            replacements.append(temporary)
            shutil.copyfile(self.source, destination)
            return SimpleNamespace(published=False, temporary_consumed=False)

        with patch(
            "code_agent.sessions.legacy_migration._publish_without_overwrite",
            side_effect=publish_then_replace_temporary,
        ):
            result = migrate_legacy_session_database(self.source, self.destination)

        self.assertFalse(result.migrated)
        self.assertIn("replaced", result.cleanup_warning or "")
        self.assertEqual(
            replacements[0].read_bytes(),
            b"belongs to another process",
        )

    def test_logically_equal_winner_can_have_a_different_page_layout(self) -> None:
        def publish_rebuilt_winner(
            temporary: object,
            destination: Path,
            expected: object,
        ) -> object:
            del temporary, expected
            _create_legacy_database(destination, page_size=8192)
            return SimpleNamespace(published=False, temporary_consumed=False)

        with patch(
            "code_agent.sessions.legacy_migration._publish_without_overwrite",
            side_effect=publish_rebuilt_winner,
        ):
            result = migrate_legacy_session_database(self.source, self.destination)

        self.assertFalse(result.migrated)
        self.assertEqual(_page_size(self.source), 4096)
        self.assertEqual(_page_size(self.destination), 8192)


def _create_legacy_database(
    path: Path,
    *,
    thread_id: str = "legacy-thread",
    task_id: str = "legacy-task",
    page_size: int | None = None,
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        if page_size is not None:
            connection.execute(f"PRAGMA page_size = {page_size}")
        connection.executescript(
            """
            CREATE TABLE threads (id TEXT PRIMARY KEY);
            CREATE TABLE tasks (id TEXT PRIMARY KEY);
            """
        )
        connection.execute("INSERT INTO threads VALUES (?)", (thread_id,))
        connection.execute("INSERT INTO tasks VALUES (?)", (task_id,))


def _ids(path: Path) -> tuple[str, str]:
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        thread_id = str(connection.execute("SELECT id FROM threads").fetchone()[0])
        task_id = str(connection.execute("SELECT id FROM tasks").fetchone()[0])
    return thread_id, task_id


def _page_size(path: Path) -> int:
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        return int(connection.execute("PRAGMA page_size").fetchone()[0])


def _validated_counts(path: Path) -> tuple[int, int]:
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("threads", "tasks")
        )
    if integrity != "ok":
        raise AssertionError(f"unexpected SQLite integrity result: {integrity}")
    return counts  # type: ignore[return-value]


if __name__ == "__main__":
    unittest.main()
