from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from itertools import islice
from typing import Callable, Iterator, Protocol, Sequence

import regex as regex_lib

from .errors import (
    BinaryFileError,
    FileTooLargeError,
    SearchTimeoutError,
    WorkspaceError,
)


MAX_SEARCH_PATTERN_LENGTH = 10_000
_MAX_VISIBLE_FILES = 10_000
_MAX_SCANNED_ENTRIES = 200_000


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line: int
    column: int
    text: str


class _TextDocument(Protocol):
    text: str


class _IterFiles(Protocol):
    def __call__(
        self,
        max_scanned_entries: int,
        check: Callable[[], None] | None = None,
    ) -> Iterator[str]: ...


class _ReadText(Protocol):
    def __call__(self, path: str) -> _TextDocument: ...


def search_text(
    iter_files: _IterFiles,
    read_text: _ReadText,
    search_timeout_s: float,
    pattern: str,
    regex: bool = False,
    case_sensitive: bool = False,
    include_globs: Sequence[str] = (),
    max_results: int = 100,
) -> tuple[SearchMatch, ...]:
    """Search text exposed by bounded workspace callbacks."""
    _validate_parameters(pattern, include_globs, max_results)
    deadline = time.monotonic() + search_timeout_s
    compiled = _compile_pattern(pattern, regex, case_sensitive)
    matches: list[SearchMatch] = []
    visible_files = islice(
        iter_files(_MAX_SCANNED_ENTRIES, lambda: _remaining(deadline)),
        _MAX_VISIBLE_FILES,
    )
    for relative_path in visible_files:
        _remaining(deadline)
        if include_globs and not _included(relative_path, include_globs):
            continue
        try:
            document = read_text(relative_path)
        except (BinaryFileError, FileTooLargeError, OSError, WorkspaceError):
            continue
        if _append_document_matches(
            matches,
            relative_path,
            document.text,
            pattern,
            compiled,
            case_sensitive,
            max_results,
            deadline,
            search_timeout_s,
        ):
            return tuple(matches)
    return tuple(matches)


def _validate_parameters(
    pattern: str,
    include_globs: Sequence[str],
    max_results: int,
) -> None:
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


def _compile_pattern(
    pattern: str,
    use_regex: bool,
    case_sensitive: bool,
) -> regex_lib.Pattern[str] | None:
    if not use_regex:
        return None
    flags = 0 if case_sensitive else regex_lib.IGNORECASE
    try:
        return regex_lib.compile(pattern, flags)
    except regex_lib.error as error:
        raise ValueError(f"invalid regular expression: {error}") from error


def _append_document_matches(
    matches: list[SearchMatch],
    relative_path: str,
    text: str,
    pattern: str,
    compiled: regex_lib.Pattern[str] | None,
    case_sensitive: bool,
    max_results: int,
    deadline: float,
    search_timeout_s: float,
) -> bool:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _append_line_matches(
            matches,
            relative_path,
            line_number,
            line,
            pattern,
            compiled,
            case_sensitive,
            max_results,
            deadline,
            search_timeout_s,
        ):
            return True
    return False


def _append_line_matches(
    matches: list[SearchMatch],
    relative_path: str,
    line_number: int,
    line: str,
    pattern: str,
    compiled: regex_lib.Pattern[str] | None,
    case_sensitive: bool,
    max_results: int,
    deadline: float,
    search_timeout_s: float,
) -> bool:
    remaining = _remaining(deadline)
    try:
        columns = _matching_columns(
            line, pattern, compiled, case_sensitive, remaining
        )
        for column in columns:
            _remaining(deadline)
            matches.append(
                SearchMatch(relative_path, line_number, column, line)
            )
            if len(matches) == max_results:
                return True
    except TimeoutError as error:
        raise SearchTimeoutError(
            f"search exceeded {search_timeout_s:g} seconds"
        ) from error
    return False


def _matching_columns(
    line: str,
    pattern: str,
    compiled: regex_lib.Pattern[str] | None,
    case_sensitive: bool,
    remaining: float,
) -> Iterator[int]:
    if compiled is not None:
        yield from (
            match.start() + 1
            for match in compiled.finditer(line, timeout=remaining)
        )
        return
    yield from _literal_columns(line, pattern, case_sensitive)


def _literal_columns(
    line: str,
    pattern: str,
    case_sensitive: bool,
) -> Iterator[int]:
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
