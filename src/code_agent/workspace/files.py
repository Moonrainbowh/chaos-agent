from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator, Sequence

from .errors import (
    BinaryFileError, CodeSliceStaleError, FileTooLargeError,
    WindowsLongPathError, WorkspaceError,
)
from code_agent.repo_paths import canonical_path_key, canonical_repo_path
from ._file_walk import iter_workspace_files
from ._known_files import known_workspace_files
from ._text_search import (
    MAX_SEARCH_PATTERN_LENGTH,
    SearchMatch,
    search_text,
)
from ._text_codec import TextCodecError, TextFileFormat, decode_text_bytes
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_INVENTORY_TTL_S = 300.0
MAX_INVENTORY_CACHE_ENTRIES = 16
MAX_CODE_SLICE_TARGETS = 16
MAX_CODE_SLICE_LINES = 400
MAX_CODE_SLICE_BYTES = 128 * 1024


@dataclass(frozen=True)
class TextDocument:
    relative_path: str
    text: str
    total_lines: int
    start_line: int
    end_line: int
    text_format: TextFileFormat


@dataclass(frozen=True)
class CodeSliceRequest:
    path: str
    start_line: int
    end_line: int
    expected_size_bytes: int
    expected_modified_ns: int
    expected_device_id: int = 0
    expected_file_id: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonical_repo_path(self.path))
        for name in (
            "start_line", "end_line", "expected_size_bytes", "expected_modified_ns",
            "expected_device_id", "expected_file_id",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("code slice range must be positive and ordered")
        if self.end_line - self.start_line + 1 > MAX_CODE_SLICE_LINES:
            raise ValueError(f"a code slice may contain at most {MAX_CODE_SLICE_LINES} lines")
        if any(getattr(self, name) < 0 for name in (
            "expected_size_bytes", "expected_modified_ns",
            "expected_device_id", "expected_file_id",
        )):
            raise ValueError("expected file signature values must not be negative")


@dataclass(frozen=True)
class CodeSlice:
    path: str
    start_line: int
    end_line: int
    total_lines: int
    text: str
    text_format: TextFileFormat


class WorkspaceFiles:
    """Bounded file discovery, text reading, and searching."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        ignore: IgnoreRules,
        *,
        search_timeout_s: float = 2.0,
        inventory_ttl_s: float = DEFAULT_INVENTORY_TTL_S,
    ) -> None:
        if isinstance(search_timeout_s, bool) or not isinstance(
            search_timeout_s, (int, float)
        ):
            raise TypeError("search_timeout_s must be a number")
        if not math.isfinite(search_timeout_s) or search_timeout_s <= 0:
            raise ValueError("search_timeout_s must be positive and finite")
        if isinstance(inventory_ttl_s, bool) or not isinstance(
            inventory_ttl_s, (int, float)
        ):
            raise TypeError("inventory_ttl_s must be a number")
        if not math.isfinite(inventory_ttl_s) or inventory_ttl_s <= 0:
            raise ValueError("inventory_ttl_s must be positive and finite")
        self.guard = guard
        self.ignore = ignore
        self.search_timeout_s = float(search_timeout_s)
        self.inventory_ttl_s = float(inventory_ttl_s)
        self._inventory_cache: OrderedDict[
            tuple[int, int], tuple[float, int | None, tuple[str, ...]]
        ] = OrderedDict()
        self._inventory_lock = RLock()
        self._inventory_invalidation_requested = False

    def list_files(
        self,
        root: str | os.PathLike[str] | None = None,
        sorted: bool = True,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_scanned_entries: int | None = None,
    ) -> tuple[str, ...]:
        """List contained, non-ignored files as POSIX relative paths."""
        if not isinstance(sorted, bool):
            raise TypeError("sorted must be a boolean")
        if not isinstance(max_entries, int) or isinstance(max_entries, bool):
            raise TypeError("max_entries must be an integer")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        scan_limit = _scan_limit(max_entries, max_scanned_entries)

        del sorted  # The underlying iterator is ordered for both modes.
        if root is not None and self.guard.resolve(root) != self.guard.root:
            return tuple(islice(self._iter_external_files(root, scan_limit), max_entries))
        key = (max_entries, scan_limit)
        with self._inventory_lock:
            if self._inventory_invalidation_requested:
                self._inventory_cache.clear()
                self._inventory_invalidation_requested = False
            now = time.monotonic()
            expired = tuple(
                cache_key
                for cache_key, cached_value in self._inventory_cache.items()
                if now >= cached_value[0]
            )
            for cache_key in expired:
                self._inventory_cache.pop(cache_key, None)
            root_signature = _directory_signature(self.guard.root)
            cached = self._inventory_cache.get(key)
            if (
                cached is not None
                and now < cached[0]
                and root_signature == cached[1]
            ):
                self._inventory_cache.move_to_end(key)
                return cached[2]
            listed = tuple(islice(self._iter_files(scan_limit), max_entries))
            if self._inventory_invalidation_requested:
                self._inventory_cache.clear()
                self._inventory_invalidation_requested = False
                return listed
            self._inventory_cache[key] = (
                now + self.inventory_ttl_s,
                root_signature,
                listed,
            )
            self._inventory_cache.move_to_end(key)
            while len(self._inventory_cache) > MAX_INVENTORY_CACHE_ENTRIES:
                self._inventory_cache.popitem(last=False)
            return listed

    def invalidate_inventory(self) -> None:
        """Discard cached workspace-root listings after possible file changes."""
        self._inventory_invalidation_requested = True
        if not self._inventory_lock.acquire(blocking=False):
            return
        try:
            self._inventory_cache.clear()
            self._inventory_invalidation_requested = False
        finally:
            self._inventory_lock.release()

    def list_known_files(
        self, candidates: Sequence[str], *, max_entries: int, max_scanned_entries: int,
    ) -> tuple[str, ...]:
        return known_workspace_files(
            candidates, self.guard, self.ignore, max_entries=max_entries,
            max_scanned_entries=max_scanned_entries,
        )
    def read_text(
        self,
        path: str | os.PathLike[str],
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        encoding: str = "auto",
    ) -> TextDocument:
        """Read an inclusive line range from a bounded, strictly decoded file."""
        if not isinstance(start_line, int) or isinstance(start_line, bool):
            raise TypeError("start_line must be an integer")
        if end_line is not None and (
            not isinstance(end_line, int) or isinstance(end_line, bool)
        ):
            raise TypeError("end_line must be an integer or None")
        if start_line < 1:
            raise ValueError("start_line must be at least 1")

        resolved = self.guard.resolve(path)
        data = _read_limited(resolved, max_bytes)
        try:
            decoded = decode_text_bytes(data, encoding)
        except TextCodecError as error:
            raise BinaryFileError(f"cannot decode text file: {resolved}") from error
        text = decoded.text
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        if total_lines == 0:
            if start_line != 1 or end_line not in (None, 0):
                raise ValueError("line range is outside the empty file")
            selected_end = 0
        else:
            selected_end = total_lines if end_line is None else end_line
            if start_line > total_lines:
                raise ValueError("start_line exceeds the file line count")
            if selected_end < start_line or selected_end > total_lines:
                raise ValueError("end_line is outside the requested range")

        selected = "" if selected_end == 0 else "".join(
            lines[start_line - 1 : selected_end]
        )
        return TextDocument(
            relative_path=self.guard.relative(resolved).as_posix(),
            text=selected,
            total_lines=total_lines,
            start_line=start_line,
            end_line=selected_end,
            text_format=decoded.format,
        )

    def read_code_slices(
        self, targets: Sequence[CodeSliceRequest]
    ) -> tuple[CodeSlice, ...]:
        """Atomically read bounded canonical ranges tied to file signatures."""
        requests = tuple(targets)
        if not requests or len(requests) > MAX_CODE_SLICE_TARGETS:
            raise ValueError(
                f"targets must contain 1 to {MAX_CODE_SLICE_TARGETS} code slices"
            )
        if not all(isinstance(item, CodeSliceRequest) for item in requests):
            raise TypeError("targets must contain CodeSliceRequest values")
        _reject_conflicting_code_ranges(requests)

        prepared: list[tuple[CodeSliceRequest, Path]] = []
        for request in requests:
            resolved = self.guard.resolve(request.path)
            _require_code_signature(resolved, request)
            prepared.append((request, resolved))

        results: list[CodeSlice] = []
        total_bytes = 0
        for request, resolved in prepared:
            document = self.read_text(
                request.path,
                start_line=request.start_line,
                end_line=request.end_line,
            )
            logical = document.text.splitlines()
            normalized_text = "\n".join(logical) + ("\n" if logical else "")
            total_bytes += len(normalized_text.encode("utf-8"))
            if total_bytes > MAX_CODE_SLICE_BYTES:
                raise FileTooLargeError(
                    f"code slices exceed {MAX_CODE_SLICE_BYTES} bytes"
                )
            results.append(CodeSlice(
                document.relative_path,
                document.start_line,
                document.end_line,
                document.total_lines,
                normalized_text,
                document.text_format,
            ))

        for request, resolved in prepared:
            _require_code_signature(resolved, request)
        return tuple(results)

    def _iter_external_files(
        self, root: str | os.PathLike[str], max_scanned_entries: int
    ) -> Iterator[str]:
        directory = self.guard.resolve(root)
        if not directory.is_dir():
            raise WorkspaceError(f"not a directory: {directory}")
        pending = [directory]
        scanned = 0
        paths: list[str] = []
        while pending:
            current = pending.pop()
            try:
                entries = list(current.iterdir())
                entries.sort(key=lambda item: item.name.casefold())
            except OSError:
                continue
            for entry in entries:
                scanned += 1
                if scanned > max_scanned_entries:
                    raise WorkspaceError(f"workspace scan exceeds {max_scanned_entries} entries")
                try:
                    resolved = self.guard.resolve(entry)
                except WindowsLongPathError:
                    raise
                except WorkspaceError:
                    continue
                if resolved.is_dir():
                    pending.append(resolved)
                elif resolved.is_file():
                    paths.append(resolved.relative_to(directory).as_posix())
        paths.sort()
        yield from paths

    def search(
        self,
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
        include_globs: Sequence[str] = (),
        max_results: int = 100,
    ) -> tuple[SearchMatch, ...]:
        """Search visible text files and return bounded, deterministic matches."""
        return search_text(
            self._iter_files,
            self.read_text,
            self.search_timeout_s,
            pattern,
            regex,
            case_sensitive,
            include_globs,
            max_results,
        )

    def _iter_files(
        self,
        max_scanned_entries: int,
        check: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        yield from iter_workspace_files(
            self.guard, self.ignore, max_scanned_entries, check
        )


def _read_limited(path: Path, max_bytes: int) -> bytes:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise TypeError("max_bytes must be an integer")
    if max_bytes < 0:
        raise ValueError("max_bytes cannot be negative")
    if not path.is_file():
        raise WorkspaceError(f"not a regular file: {path}")
    try:
        if path.stat().st_size > max_bytes:
            raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {path}")
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except FileTooLargeError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot read file: {path}") from error
    if len(data) > max_bytes:
        raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {path}")
    return data


def _require_code_signature(path: Path, request: CodeSliceRequest) -> None:
    try:
        metadata = path.stat()
    except OSError as error:
        raise CodeSliceStaleError(f"code slice path is stale: {request.path}") from error
    current = (
        metadata.st_size,
        metadata.st_mtime_ns,
        int(getattr(metadata, "st_dev", 0)),
        int(getattr(metadata, "st_ino", 0)),
    )
    expected = (
        request.expected_size_bytes,
        request.expected_modified_ns,
        request.expected_device_id,
        request.expected_file_id,
    )
    if not path.is_file() or current != expected:
        raise CodeSliceStaleError(f"code slice signature is stale: {request.path}")


def _reject_conflicting_code_ranges(targets: Sequence[CodeSliceRequest]) -> None:
    previous: dict[str, list[tuple[int, int]]] = {}
    for target in targets:
        key = canonical_path_key(target.path)
        ranges = previous.setdefault(key, [])
        if any(target.start_line <= end and start <= target.end_line for start, end in ranges):
            raise ValueError(f"code slice ranges overlap: {target.path}")
        ranges.append((target.start_line, target.end_line))


def _scan_limit(max_entries: int, supplied: int | None) -> int:
    if supplied is None:
        return max(1_000, max_entries * 20)
    if not isinstance(supplied, int) or isinstance(supplied, bool):
        raise TypeError("max_scanned_entries must be an integer")
    if supplied <= 0:
        raise ValueError("max_scanned_entries must be positive")
    return supplied


def _directory_signature(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None
