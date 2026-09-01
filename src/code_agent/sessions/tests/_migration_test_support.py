from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from code_agent.sessions._database import _MIGRATIONS


def create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        PRAGMA user_version = 1;
        """
    )
    connection.commit()
    connection.close()


def create_v3_database(path: Path) -> None:
    create_v1_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            ALTER TABLE threads ADD COLUMN title TEXT;
            ALTER TABLE threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
            CREATE TABLE goals (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, objective TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE checkpoints (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, label TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX goals_thread_created ON goals(thread_id, created_at, id);
            CREATE INDEX checkpoints_thread_created ON checkpoints(thread_id, created_at, id);
            CREATE TABLE task_budgets (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, model_name TEXT NOT NULL, max_agent_rounds INTEGER NOT NULL, max_tool_calls INTEGER NOT NULL, max_tool_calls_per_round INTEGER NOT NULL, model_turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version = 3;
            """
        )


def advance_v3_database(path: Path, target: int) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        for version in range(4, target + 1):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {target}")


def insert_legacy_checkpoint(path: Path) -> None:
    timestamp = "2026-07-11T00:00:00Z"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            ("legacy", timestamp, timestamp, None, "active"),
        )
        connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
            ("checkpoint", "legacy", "old", "{}", timestamp),
        )
        connection.commit()
    finally:
        connection.close()
