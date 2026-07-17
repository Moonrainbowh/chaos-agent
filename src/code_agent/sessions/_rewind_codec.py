from __future__ import annotations

import base64
import binascii
import json
from typing import Mapping, cast

from code_agent.core._json import (
    JSONValue,
    freeze_mapping,
    plain,
    validate_json_mapping,
)

from ._codec import decode_datetime, encode_datetime
from .errors import SessionCorruptionError


_MAX_HANDLE_BYTES = 64 * 1024
_MAX_CURSOR_LENGTH = 1024
_MAX_TEXT_FIELD = 512
_CURSOR_ERROR = "invalid rewind cursor"


def encode_rewind_handle(value: Mapping[str, JSONValue]) -> str:
    validate_json_mapping(value, "rewind snapshot handle")
    encoded = json.dumps(
        plain(cast(JSONValue, value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        encoded_size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("rewind snapshot handle is not valid UTF-8") from error
    if encoded_size > _MAX_HANDLE_BYTES:
        raise ValueError("rewind snapshot handle exceeds 64 KiB")
    return encoded


def decode_rewind_handle(value: object) -> Mapping[str, JSONValue]:
    try:
        if not isinstance(value, str):
            raise TypeError
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError
        frozen = freeze_mapping(decoded, "rewind snapshot handle")
        if encode_rewind_handle(frozen) != value:
            raise ValueError
        return frozen
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid rewind snapshot handle") from error


def encode_rewind_cursor(created_at: str, checkpoint_id: str) -> str:
    if not isinstance(created_at, str) or not isinstance(checkpoint_id, str):
        raise TypeError("rewind cursor fields must be strings")
    if (
        not created_at.strip()
        or len(created_at) > _MAX_TEXT_FIELD
        or not checkpoint_id.strip()
        or len(checkpoint_id) > _MAX_TEXT_FIELD
    ):
        raise ValueError("checkpoint_id is invalid")
    try:
        canonical_time = encode_datetime(decode_datetime(created_at, "rewind cursor"))
    except SessionCorruptionError as error:
        raise ValueError(_CURSOR_ERROR) from error
    if canonical_time != created_at:
        raise ValueError(_CURSOR_ERROR)
    payload = json.dumps(
        {"created_at": created_at, "id": checkpoint_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(token) > _MAX_CURSOR_LENGTH:
        raise ValueError(_CURSOR_ERROR)
    return token


def decode_rewind_cursor(value: str) -> tuple[str, str]:
    try:
        if (
            not isinstance(value, str)
            or not value
            or "=" in value
            or len(value) > _MAX_CURSOR_LENGTH
        ):
            raise ValueError
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            value + padding, altchars=b"-_", validate=True
        )
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or set(decoded) != {"created_at", "id"}:
            raise ValueError
        created_at = decoded["created_at"]
        checkpoint_id = decoded["id"]
        if not isinstance(created_at, str) or not isinstance(checkpoint_id, str):
            raise ValueError
        if encode_rewind_cursor(created_at, checkpoint_id) != value:
            raise ValueError
        return created_at, checkpoint_id
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(_CURSOR_ERROR) from error
