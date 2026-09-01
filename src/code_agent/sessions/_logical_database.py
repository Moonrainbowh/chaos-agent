from __future__ import annotations

import hashlib
import sqlite3
import struct
from collections.abc import Iterable, Sequence


def logical_database_digest(connection: sqlite3.Connection) -> str:
    """Hash logical schema and row values without depending on SQLite page bytes."""
    digest = hashlib.sha256()
    _feed_values(
        digest,
        (
            "database-header",
            int(connection.execute("PRAGMA user_version").fetchone()[0]),
            int(connection.execute("PRAGMA application_id").fetchone()[0]),
        ),
    )
    schema = tuple(
        connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name, tbl_name, sql"
        )
    )
    _feed_rows(digest, "schema", schema)
    tables = sorted(
        str(row[1]) for row in schema if str(row[0]) == "table"
    )
    for table in tables:
        rows = connection.execute(f"SELECT * FROM {_quote_identifier(table)}")
        _feed_rows(digest, f"table:{table}", rows)
    internal = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }
    if "sqlite_sequence" in internal:
        _feed_rows(
            digest,
            "table:sqlite_sequence",
            connection.execute("SELECT name, seq FROM sqlite_sequence"),
        )
    else:
        _feed_rows(digest, "table:sqlite_sequence:absent", ())
    return digest.hexdigest()


def _feed_rows(
    digest: object,
    label: str,
    rows: Iterable[Sequence[object]],
) -> None:
    row_digests = sorted(_row_digest(row) for row in rows)
    _feed_values(digest, (label, len(row_digests)))
    for row_digest in row_digests:
        digest.update(row_digest)  # type: ignore[attr-defined]


def _row_digest(row: Sequence[object]) -> bytes:
    digest = hashlib.sha256()
    _feed_values(digest, row)
    return digest.digest()


def _feed_values(digest: object, values: Sequence[object]) -> None:
    digest.update(len(values).to_bytes(8, "big"))  # type: ignore[attr-defined]
    for value in values:
        encoded = _encode_value(value)
        digest.update(len(encoded).to_bytes(8, "big"))  # type: ignore[attr-defined]
        digest.update(encoded)  # type: ignore[attr-defined]


def _encode_value(value: object) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"f" + struct.pack(">d", value)
    if isinstance(value, str):
        return b"t" + value.encode("utf-8")
    if isinstance(value, bytes):
        return b"b" + value
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
