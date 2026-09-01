from __future__ import annotations

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

from code_agent.sessions.legacy_migration import (  # noqa: E402
    migrate_legacy_session_database,
)


@unittest.skipUnless(os.name == "nt", "Windows handle-bound publication")
class LegacyPublicationIdentityTests(unittest.TestCase):
    def test_path_replacement_after_identity_check_cannot_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.sqlite3"
            destination = root / "current" / "sessions.sqlite3"
            _create_database(source, "expected-thread", "expected-task")
            replacement_blocked: list[bool] = []
            real_rename = os.rename

            def replace_path(_handle: int, path: Path, _destination: Path) -> None:
                displaced = path.with_name(path.name + ".displaced")
                try:
                    real_rename(path, displaced)
                except PermissionError:
                    replacement_blocked.append(True)
                    return
                replacement_blocked.append(False)
                _create_database(path, "foreign-thread", "foreign-task")

            with patch(
                "code_agent._windows_owned_temporary._before_windows_publish",
                side_effect=replace_path,
            ):
                result = migrate_legacy_session_database(source, destination)

            self.assertTrue(result.migrated)
            self.assertEqual(replacement_blocked, [True])
            self.assertEqual(_ids(destination), ("expected-thread", "expected-task"))


def _create_database(path: Path, thread_id: str, task_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            "CREATE TABLE threads(id TEXT PRIMARY KEY);"
            "CREATE TABLE tasks(id TEXT PRIMARY KEY);"
        )
        connection.execute("INSERT INTO threads VALUES (?)", (thread_id,))
        connection.execute("INSERT INTO tasks VALUES (?)", (task_id,))


def _ids(path: Path) -> tuple[str, str]:
    with closing(sqlite3.connect(path)) as connection:
        thread = str(connection.execute("SELECT id FROM threads").fetchone()[0])
        task = str(connection.execute("SELECT id FROM tasks").fetchone()[0])
    return thread, task


if __name__ == "__main__":
    unittest.main()
