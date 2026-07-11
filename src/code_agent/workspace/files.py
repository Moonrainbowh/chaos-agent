from __future__ import annotations

import fnmatch
import math
import os
import time
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator, Sequence

import regex as regex_lib

from .errors import (
    BinaryFileError,
    FileTooLargeError,
    SearchTimeoutError,
    WorkspaceError,
)
from ._file_walk import iter_workspace_files
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_ENTRIES = 10_000
MAX_SEARCH_PATTERN_LENGTH = 10_000


@dataclass(frozen=True)
class TextDocument:
    relative_path: str
    text: str
    total_lines: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line: int
    column: int
    text: str


class WorkspaceFiles:
    """Bounded file discovery, text reading, and searching."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        ignore: IgnoreRules,
        *,
        search_timeout_s: float = 2.0,
    ) -> None:
        if isinstance(search_timeout_s, bool) or not isinstance(
            search_timeout_s, (int, float)
        ):
            raise TypeError("search_timeout_s must be a number")
        if not math.isfinite(search_timeout_s) or search_timeout_s <= 0:
            raise ValueError("search_timeout_s must be positive and finite")
        self.guard = guard
        self.ignore = ignore
        self.search_timeout_s = float(search_timeout_s)

    def list_files(
        self,
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
        return tuple(islice(self._iter_files(scan_limit), max_entries))

    def read_text(
        self,
        path: str | os.PathLike[str],
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> TextDocument:
        """Read an inclusive line range from a bounded UTF-8 text file."""
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
        text = _decode_text(data, resolved)
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
        )

    def search(
        self,
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
        include_globs: Sequence[str] = (),
        max_results: int = 100,
    ) -> tuple[SearchMatch, ...]:
        """Search visible text files and return bounded, deterministic matches."""
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("search pattern must be non-empty text")
        if len(pattern) > MAX_SEARCH_PATTERN_LENGTH:
            raise ValueError(
                f"search pattern exceeds {MAX_SEARCH_PATTERN_LENGTH} characters"
            )
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise TypeError("max_results must be an integer")
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        if any(not isinstance(item, str) or not item for item in include_globs):
            raise ValueError("include_globs must contain non-empty strings")

        deadline = time.monotonic() + self.search_timeout_s
        compiled: regex_lib.Pattern[str] | None = None
        if regex:
            flags = 0 if case_sensitive else regex_lib.IGNORECASE
            try:
                compiled = regex_lib.compile(pattern, flags)
            except regex_lib.error as error:
                raise ValueError(f"invalid regular expression: {error}") from error

        matches: list[SearchMatch] = []
        scan_limit = _scan_limit(DEFAULT_MAX_ENTRIES, None)
        visible_files = islice(
            self._iter_files(
                scan_limit, check=lambda: _remaining(deadline)
            ),
            DEFAULT_MAX_ENTRIES,
        )
        for relative_path in visible_files:
            _remaining(deadline)
            if include_globs and not _included(relative_path, include_globs):
                continue
            try:
                document = self.read_text(relative_path)
            except (BinaryFileError, FileTooLargeError, OSError, WorkspaceError):
                continue
            for line_number, line in enumerate(document.text.splitlines(), start=1):
                remaining = _remaining(deadline)
                try:
                    columns = (
                        (
                            match.start() + 1
                            for match in compiled.finditer(
                                line, timeout=remaining
                            )
                        )
                        if compiled is not None
                        else _literal_columns(line, pattern, case_sensitive)
                    )
                    for column in columns:
                        _remaining(deadline)
                        matches.append(
                            SearchMatch(relative_path, line_number, column, line)
                        )
                        if len(matches) == max_results:
                            return tuple(matches)
                except TimeoutError as error:
                    raise SearchTimeoutError(
                        f"search exceeded {self.search_timeout_s:g} seconds"
                    ) from error
        return tuple(matches)

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


def _decode_text(data: bytes, path: Path) -> str:
    if b"\0" in data:
        raise BinaryFileError(f"NUL byte in text file: {path}")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BinaryFileError(f"file is not valid UTF-8: {path}") from error


def _literal_columns(line: str, pattern: str, case_sensitive: bool) -> Iterator[int]:
    haystack = line if case_sensitive else line.casefold()
    needle = pattern if case_sensitive else pattern.casefold()
    offset = 0
    while (found := haystack.find(needle, offset)) >= 0:
        yield found + 1
        offset = found + len(needle)


def _included(path: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        candidates = (normalized, normalized.removeprefix("**/"))
        for candidate in candidates:
            subject = path if "/" in candidate else path.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(subject, candidate):
                return True
    return False


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SearchTimeoutError("search deadline exceeded")
    return remaining


def _scan_limit(max_entries: int, supplied: int | None) -> int:
    if supplied is None:
        return max(1_000, max_entries * 20)
    if not isinstance(supplied, int) or isinstance(supplied, bool):
        raise TypeError("max_scanned_entries must be an integer")
    if supplied <= 0:
        raise ValueError("max_scanned_entries must be positive")
    return supplied
