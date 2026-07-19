from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TypeVar


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_T = TypeVar("_T")


def validate_text(value: object, field: str, *, maximum: int = 512) -> str:
    """Return a bounded, nonblank string without changing its wire value."""
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    if len(value) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} characters")
    return value


def validate_optional_cursor(value: object) -> str | None:
    """Validate an opaque cursor while preserving empty and whitespace values."""
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("next_cursor must be a string or None")
    if len(value) > 1024:
        raise ValueError("next_cursor must contain at most 1024 characters")
    return value


def validate_bool(value: object, field: str) -> bool:
    """Require a real bool rather than an integer-compatible value."""
    if type(value) is not bool:
        raise TypeError(f"{field} must be a bool")
    return value


def validate_nonnegative_int(
    value: object, field: str, *, optional: bool = False
) -> int | None:
    """Require None when allowed, or a real nonnegative integer."""
    if optional and value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be nonnegative")
    return value


def validate_positive_int(
    value: object, field: str, *, optional: bool = False
) -> int | None:
    """Require None when allowed, or a real positive integer."""
    if optional and value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def validate_digest(value: object) -> str | None:
    """Require a lowercase SHA-256 hex digest or None."""
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("relevant_path_digest must be a string or None")
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("relevant_path_digest must be lowercase SHA-256 hex")
    return value


def normalize_utc(value: object, field: str) -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def validate_path(value: object) -> str:
    """Require a bounded canonical relative POSIX path."""
    path = validate_text(value, "path")
    if "\0" in path:
        raise ValueError("path must not contain NUL")
    if "\\" in path:
        raise ValueError("path must use POSIX separators")
    if path.startswith("/"):
        raise ValueError("path must be relative")
    if re.match(r"[A-Za-z]:", path):
        raise ValueError("path must not begin with a drive-like prefix")
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError("path must be canonical")
    return path


def validate_exact_tuple(
    value: object,
    field: str,
    item_type: type[_T],
) -> tuple[_T, ...]:
    """Require an exact tuple containing only exact instances of item_type."""
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    if any(type(item) is not item_type for item in value):
        raise TypeError(f"{field} contains an invalid item")
    return value


def validate_unique_strings(values: tuple[str, ...], field: str) -> None:
    """Reject duplicate identifiers while retaining caller order."""
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")
