from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from code_agent._owned_temporary import (
    OwnedTemporary,
    OwnedTemporaryReplacedError,
)
from code_agent.core.attachments import AttachmentRef

from .errors import (
    AttachmentCommittedError,
    AttachmentError,
    AttachmentIntegrityError,
)


_COMMITTED_READ_TIMEOUT_S = 0.5
_READ_RETRY_WINERRORS = frozenset({5, 32, 33})
_INITIAL_RETRY_DELAY_S = 0.01
_MAX_RETRY_DELAY_S = 0.1


@dataclass(frozen=True)
class _WriteResult:
    published: bool
    cleanup_warning: str | None = None


class AttachmentStore:
    """Atomic SHA-256 blob storage whose public references contain no path."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)
        _require_plain_directory(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        display_name: str,
        width: int | None = None,
        height: int | None = None,
    ) -> AttachmentRef:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        reference = AttachmentRef(
            digest, media_type, len(data), display_name, width, height
        )
        directory = self._root / digest[:2]
        directory.mkdir(exist_ok=True)
        _require_plain_directory(directory)
        destination = self._blob_path(reference)
        if destination.exists():
            self.read(reference)
            return reference
        write = self._write_atomic(directory, destination, data)
        if write.published:
            self._read_after_commit(reference)
        else:
            self.read(reference)
        if write.cleanup_warning is not None:
            raise AttachmentCommittedError(write.cleanup_warning, reference)
        return reference

    def read(self, reference: AttachmentRef) -> bytes:
        if not isinstance(reference, AttachmentRef):
            raise TypeError("reference must be an AttachmentRef")
        path = self._blob_path(reference)
        _require_plain_file(path)
        try:
            with path.open("rb") as stream:
                data = stream.read(reference.size_bytes + 1)
        except OSError as error:
            raise AttachmentIntegrityError(
                "attachment blob cannot be read"
            ) from error
        if len(data) != reference.size_bytes:
            raise AttachmentIntegrityError("attachment blob size does not match reference")
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise AttachmentIntegrityError("attachment blob digest does not match reference")
        return data

    def _blob_path(self, reference: AttachmentRef) -> Path:
        directory = self._root / reference.sha256[:2]
        path = directory / f"{reference.sha256}.blob"
        try:
            if path.parent.resolve(strict=False) != directory:
                raise AttachmentIntegrityError("attachment blob path is invalid")
            directory.relative_to(self._root)
        except (OSError, ValueError):
            raise AttachmentIntegrityError("attachment blob path escapes store") from None
        return path

    @staticmethod
    def _write_atomic(
        directory: Path,
        destination: Path,
        data: bytes,
    ) -> _WriteResult:
        temporary_path: Path | None = None
        temporary: OwnedTemporary | None = None
        outcome: _WriteResult | None = None
        failure: BaseException | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="xb", dir=directory, prefix=".pending-", delete=False
            ) as stream:
                temporary_path = Path(stream.name)
                temporary = OwnedTemporary.capture_cleanup_descriptor(
                    temporary_path, stream.fileno()
                )
                temporary = OwnedTemporary.capture_descriptor(
                    temporary_path,
                    stream.fileno(),
                )
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                consumed = temporary.publish_no_replace(
                    destination,
                    lambda path: _require_matching_blob(path, data),
                )
                if consumed:
                    temporary = None
                outcome = _WriteResult(True)
            except FileExistsError:
                _require_matching_blob(destination, data)
                outcome = _WriteResult(False)
        except AttachmentError as error:
            failure = error
            raise
        except OSError as error:
            if getattr(error, "publication_committed", False):
                temporary = None
                outcome = _WriteResult(
                    True,
                    "attachment publication committed but handle "
                    f"finalization failed: {_error_summary(error)}",
                )
            else:
                wrapped = AttachmentError("attachment blob could not be stored")
                failure = wrapped
                raise wrapped from error
        except BaseException as error:
            failure = error
            raise
        finally:
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OwnedTemporaryReplacedError as error:
                    detail = str(error)
                    if failure is not None:
                        add_note = getattr(failure, "add_note", None)
                        if callable(add_note):
                            add_note(detail)
                    elif outcome is not None:
                        outcome = replace(outcome, cleanup_warning=detail)
                except OSError as error:
                    detail = (
                        "owned attachment temporary cleanup failed: "
                        f"{_error_summary(error)}"
                    )
                    if failure is not None:
                        add_note = getattr(failure, "add_note", None)
                        if callable(add_note):
                            add_note(detail)
                    elif outcome is not None:
                        outcome = replace(outcome, cleanup_warning=detail)
        if outcome is None:
            raise AttachmentError("attachment blob storage produced no result")
        return outcome

    def _read_after_commit(self, reference: AttachmentRef) -> bytes:
        if os.name != "nt":
            return self.read(reference)
        deadline = time.monotonic() + _COMMITTED_READ_TIMEOUT_S
        delay = _INITIAL_RETRY_DELAY_S
        while True:
            try:
                return self.read(reference)
            except AttachmentIntegrityError as error:
                if _windows_error_code(error) not in _READ_RETRY_WINERRORS:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AttachmentCommittedError(
                        "attachment blob was committed but remained locked "
                        "during verification",
                        reference,
                    ) from error
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, _MAX_RETRY_DELAY_S)


def _require_matching_blob(path: Path, expected: bytes) -> None:
    _require_plain_file(path)
    try:
        with path.open("rb") as stream:
            actual = stream.read(len(expected) + 1)
    except OSError:
        raise AttachmentIntegrityError(
            "competing attachment blob cannot be read"
        ) from None
    expected_digest = hashlib.sha256(expected).hexdigest()
    if actual != expected or hashlib.sha256(actual).hexdigest() != expected_digest:
        raise AttachmentIntegrityError(
            "competing attachment blob does not match published content"
        )


def _windows_error_code(error: BaseException) -> int | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        code = getattr(current, "winerror", None)
        if isinstance(code, int):
            return code
        if isinstance(current, PermissionError) and current.errno == errno.EACCES:
            # CPython's Windows io.open path can collapse sharing violations to EACCES.
            return 5
        current = current.__cause__
    return None


def _error_summary(error: OSError) -> str:
    code = getattr(error, "winerror", None)
    suffix = f" ({code})" if code is not None else ""
    return f"{type(error).__name__}{suffix}"


def _require_plain_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AttachmentIntegrityError("attachment store directory is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or _is_link_like(path, metadata):
        raise AttachmentIntegrityError("attachment store directory is not plain")


def _require_plain_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise AttachmentIntegrityError("attachment blob is missing") from error
    if not stat.S_ISREG(metadata.st_mode) or _is_link_like(path, metadata):
        raise AttachmentIntegrityError("attachment blob is not a plain file")


def _is_link_like(path: Path, metadata: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & flag)
