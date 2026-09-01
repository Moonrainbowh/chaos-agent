from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .edits import SnapshotEntry
from .errors import FileTooLargeError, WorkspaceError


_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset(
    {"version", "identifier", "workspace_fingerprint", "entries", "total_bytes"}
)
_ENTRY_KEYS = frozenset({"path", "existed", "digest", "size"})


@dataclass(frozen=True)
class StoredEntry:
    path: str
    existed: bool
    digest: str | None
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "existed": self.existed,
            "digest": self.digest,
            "size": self.size,
        }


def stored_entry(
    path: str, source: SnapshotEntry, digest_content: Callable[[bytes], str]
) -> tuple[StoredEntry, bytes | None]:
    if type(source.existed) is not bool:
        raise TypeError("snapshot entry existence must be boolean")
    if not source.existed:
        if source.content is not None:
            raise ValueError("missing snapshot entries cannot contain bytes")
        return StoredEntry(path, False, None, 0), None
    if type(source.content) is not bytes:
        raise TypeError("existing snapshot entries must contain bytes")
    digest = digest_content(source.content)
    return StoredEntry(path, True, digest, len(source.content)), source.content


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def decode_manifest(raw: bytes) -> object:
    try:
        payload = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_json_constant
        )
        encoded = canonical_json(payload)
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise WorkspaceError("snapshot manifest is not valid canonical JSON") from error
    if encoded != raw:
        raise WorkspaceError("snapshot manifest is not canonical JSON")
    return payload


def validate_manifest(
    payload: object,
    *,
    identifier: str,
    workspace_fingerprint: str,
    handle_paths: tuple[str, ...],
    handle_total_bytes: int,
    max_total_bytes: int,
    canonicalize_path: Callable[[object], str],
) -> tuple[StoredEntry, ...]:
    manifest = _manifest_object(payload)
    _validate_header(manifest, identifier, workspace_fingerprint)
    raw_entries = manifest["entries"]
    if type(raw_entries) is not list:
        raise WorkspaceError("snapshot manifest entries must be a list")
    entries = tuple(_manifest_entry(value, canonicalize_path) for value in raw_entries)
    _validate_totals(
        manifest["total_bytes"],
        entries,
        handle_paths,
        handle_total_bytes,
        max_total_bytes,
    )
    return entries


def _manifest_object(payload: object) -> dict[str, object]:
    if type(payload) is not dict or frozenset(payload) != _MANIFEST_KEYS:
        raise WorkspaceError("snapshot manifest fields are invalid")
    return payload


def _validate_header(
    payload: dict[str, object], identifier: str, workspace_fingerprint: str
) -> None:
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise WorkspaceError("snapshot manifest version is invalid")
    declared_identifier = payload["identifier"]
    if not is_identifier(declared_identifier) or declared_identifier != identifier:
        raise WorkspaceError("snapshot manifest identifier does not match")
    declared_fingerprint = payload["workspace_fingerprint"]
    if not is_digest(declared_fingerprint):
        raise WorkspaceError("snapshot workspace fingerprint is invalid")
    if declared_fingerprint != workspace_fingerprint:
        raise WorkspaceError("snapshot belongs to a different workspace")


def _manifest_entry(
    value: object, canonicalize_path: Callable[[object], str]
) -> StoredEntry:
    if type(value) is not dict or frozenset(value) != _ENTRY_KEYS:
        raise WorkspaceError("snapshot manifest entry fields are invalid")
    path = canonicalize_path(value["path"])
    existed, digest, size = value["existed"], value["digest"], value["size"]
    if type(existed) is not bool or type(size) is not int or size < 0:
        raise WorkspaceError("snapshot manifest entry types are invalid")
    if existed and not is_digest(digest):
        raise WorkspaceError("snapshot blob digest is invalid")
    if not existed and (digest is not None or size != 0):
        raise WorkspaceError("missing snapshot entries cannot declare bytes")
    return StoredEntry(path, existed, digest if existed else None, size)


def _validate_totals(
    declared: object,
    entries: tuple[StoredEntry, ...],
    handle_paths: tuple[str, ...],
    handle_total_bytes: int,
    max_total_bytes: int,
) -> None:
    if type(declared) is not int or declared < 0:
        raise WorkspaceError("snapshot manifest total is invalid")
    paths = tuple(entry.path for entry in entries)
    if len({os.path.normcase(path) for path in paths}) != len(paths):
        raise WorkspaceError("snapshot manifest contains duplicate paths")
    total = sum(entry.size for entry in entries)
    if declared != total or declared != handle_total_bytes or paths != handle_paths:
        raise WorkspaceError("snapshot manifest does not match its handle")
    if total > max_total_bytes:
        raise FileTooLargeError("snapshot exceeds its total byte limit")


def is_identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def is_relative_path(value: str) -> bool:
    windows = PureWindowsPath(value)
    if (
        not value
        or "\0" in value
        or ":" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or Path(value).is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
        or (os.name == "nt" and _has_win32_alias_component(value))
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


_WIN32_DEVICES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
)


def _has_win32_alias_component(value: str) -> bool:
    for part in value.split("/"):
        if part.endswith((".", " ")):
            return True
        if any(ord(char) < 32 or char in '<>"|?*' for char in part):
            return True
        base = part.split(".", 1)[0].casefold()
        if base in _WIN32_DEVICES or re.fullmatch(r"(?:com|lpt)[1-9¹²³]", base):
            return True
    return False


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")
