from __future__ import annotations

import ctypes
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent import _windows_owned_temporary  # noqa: E402
from code_agent.sessions.errors import SessionMigrationError  # noqa: E402
from code_agent.sessions.legacy_migration import (  # noqa: E402
    migrate_legacy_session_database,
)


class LegacyMigrationFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "legacy.sqlite3"
        self.destination = self.root / "current" / "sessions.sqlite3"
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
            connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO threads VALUES ('thread')")
            connection.execute("INSERT INTO tasks VALUES ('task')")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows committed publication")
    def test_close_failure_after_rename_reports_migrated_with_warning(self) -> None:
        real_rename = _windows_owned_temporary._rename_relative
        real_close = _windows_owned_temporary._close
        renamed = False
        failed = False

        def rename(*args: object) -> None:
            nonlocal renamed
            real_rename(*args)  # type: ignore[arg-type]
            renamed = True

        def close(handle: int) -> None:
            nonlocal failed
            real_close(handle)
            if renamed and not failed:
                failed = True
                raise ctypes.WinError(5)

        with patch.object(
            _windows_owned_temporary, "_rename_relative", side_effect=rename
        ), patch.object(
            _windows_owned_temporary, "_close", side_effect=close
        ):
            result = migrate_legacy_session_database(
                self.source, self.destination
            )

        self.assertTrue(result.migrated)
        self.assertIn("finalization", result.cleanup_warning or "")
        self.assertEqual(_counts(self.destination), (1, 1))

    def test_identity_capture_failure_closes_fd_and_cleans_temp(self) -> None:
        created: list[tuple[int, Path]] = []
        real_mkstemp = tempfile.mkstemp

        def create(*args: object, **kwargs: object) -> tuple[int, str]:
            descriptor, name = real_mkstemp(*args, **kwargs)
            created.append((descriptor, Path(name)))
            return descriptor, name

        try:
            with patch(
                "code_agent.sessions.legacy_migration.tempfile.mkstemp",
                side_effect=create,
            ), patch(
                "code_agent.sessions.legacy_migration.OwnedTemporary.capture_descriptor",
                side_effect=OSError("identity unavailable"),
            ), self.assertRaises(SessionMigrationError):
                migrate_legacy_session_database(self.source, self.destination)

            descriptor, path = created[0]
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertFalse(path.exists())
        finally:
            for descriptor, path in created:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                path.unlink(missing_ok=True)


def _counts(path: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(path)) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("threads", "tasks")
        )  # type: ignore[return-value]


if __name__ == "__main__":
    unittest.main()
