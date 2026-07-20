from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ._diff_parser import DiffLine, DiffLineKind, DiffScope, FileDiff, parse_files
from .terminal_display import DisplayEntry, DisplayKind, text_entry


@dataclass(frozen=True)
class DiffSourceDocument:
    scope: DiffScope
    unified: str
    fresh: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, DiffScope) or not isinstance(self.unified, str):
            raise TypeError("diff document requires a scope and text")
        if not isinstance(self.fresh, bool):
            raise TypeError("diff document freshness must be boolean")


@dataclass(frozen=True)
class DiffComment:
    path: str
    line_index: int
    text: str
    scope: DiffScope = DiffScope.PER_TURN


DiffSourcePayload = str | DiffSourceDocument | Sequence[DiffSourceDocument]


class GitDiffSource(Protocol):
    async def read_diff(self, paths: tuple[str, ...] = ()) -> DiffSourcePayload: ...


class DiffView:
    def __init__(self, files: Sequence[FileDiff], *, stale: bool = False) -> None:
        self._files = tuple(files)
        self.stale = bool(stale)
        self.selected_file = 0
        self.path_filter = ""
        self._comments: list[DiffComment] = []

    @classmethod
    def parse(
        cls,
        unified: str,
        scope: DiffScope = DiffScope.PER_TURN,
        fresh: bool = False,
    ) -> "DiffView":
        if not isinstance(unified, str):
            raise TypeError("unified diff must be text")
        return cls.from_documents((DiffSourceDocument(scope, unified, fresh),))

    @classmethod
    def from_documents(
        cls, documents: Sequence[DiffSourceDocument], *, stale: bool = False
    ) -> "DiffView":
        files: list[FileDiff] = []
        for document in documents:
            if not isinstance(document, DiffSourceDocument):
                raise TypeError("diff documents must be DiffSourceDocument values")
            files.extend(parse_files(document.unified, document.scope, document.fresh))
        return cls(files, stale=stale)

    @property
    def files(self) -> tuple[FileDiff, ...]:
        query = self.path_filter.casefold().strip()
        return tuple(file for file in self._files if not query or query in file.path.casefold())

    @property
    def current(self) -> FileDiff | None:
        files = self.files
        return None if not files else files[min(self.selected_file, len(files) - 1)]

    def filter(self, path: str) -> None:
        if not isinstance(path, str) or len(path) > 512:
            raise ValueError("path filter must be bounded text")
        self.path_filter = path
        self.selected_file = 0

    def move_file(self, offset: int) -> None:
        files = self.files
        if files:
            self.selected_file = (self.selected_file + offset) % len(files)

    def comment(self, line_index: int, text: str) -> DiffComment:
        current = self.current
        if current is None:
            raise ValueError("no file is selected")
        if not isinstance(line_index, int) or isinstance(line_index, bool) or line_index < 0:
            raise ValueError("line_index is outside the selected diff")
        if line_index >= len(current.lines):
            raise ValueError("line_index is outside the selected diff")
        if not isinstance(text, str) or not text.strip() or len(text) > 2_048:
            raise ValueError("comment must be bounded non-blank text")
        comment = DiffComment(current.path, line_index, text, current.scope)
        self._comments.append(comment)
        return comment

    @property
    def comments(self) -> tuple[DiffComment, ...]:
        return tuple(self._comments)

    def render(self, *, max_lines: int = 200) -> tuple[DisplayEntry, ...]:
        if not isinstance(max_lines, int) or isinstance(max_lines, bool) or max_lines < 0:
            raise ValueError("max_lines must be a non-negative integer")
        current = self.current
        if current is None:
            return (text_entry(DisplayKind.METADATA, "no diff available"),)
        entries = [
            text_entry(
                DisplayKind.METADATA,
                _diff_header(current, self.stale, self.selected_file + 1, len(self.files)),
            )
        ]
        kinds = {
            DiffLineKind.ADD: DisplayKind.DIFF_ADD,
            DiffLineKind.REMOVE: DisplayKind.DIFF_REMOVE,
            DiffLineKind.HUNK: DisplayKind.METADATA,
            DiffLineKind.CONTEXT: DisplayKind.METADATA,
        }
        entries.extend(text_entry(kinds[line.kind], line.text) for line in current.lines[:max_lines])
        if len(current.lines) > max_lines:
            entries.append(text_entry(DisplayKind.METADATA, f"... {len(current.lines) - max_lines} lines hidden"))
        return tuple(entries)


class DiffController:
    def __init__(self, source: GitDiffSource | None = None) -> None:
        self._source = source

    async def load(
        self,
        scope: DiffScope,
        recorded_diff: str | None = None,
        paths: tuple[str, ...] = (),
    ) -> DiffView:
        if not isinstance(scope, DiffScope):
            raise TypeError("diff scope must be a DiffScope")
        recorded = _recorded_text(recorded_diff)
        if scope in _RECORDED_SCOPES:
            documents = _recorded_documents(scope, recorded)
            return DiffView.from_documents(documents)
        payload: DiffSourcePayload = ""
        if self._source is not None:
            payload = await self._source.read_diff(paths)
        documents = _select_live_documents(_normalize_payload(payload, scope), scope)
        live = "".join(document.unified for document in documents)
        stale = bool(recorded.strip()) and _normalized(live) != _normalized(recorded)
        live_view = DiffView.from_documents(documents, stale=stale)
        if live_view.files:
            return live_view
        if recorded.strip():
            fallback = DiffSourceDocument(scope, recorded, False)
            return DiffView.from_documents((fallback,), stale=True)
        return live_view


_RECORDED_SCOPES = frozenset({DiffScope.PER_TURN, DiffScope.SINCE_CHECKPOINT})
_WORKING_TREE_FACETS = (DiffScope.STAGED, DiffScope.UNSTAGED, DiffScope.UNTRACKED)


def _recorded_text(recorded: str | None) -> str:
    if recorded is None:
        return ""
    if not isinstance(recorded, str):
        raise TypeError("recorded diff must be text or None")
    return recorded


def _recorded_documents(
    scope: DiffScope, recorded: str
) -> tuple[DiffSourceDocument, ...]:
    if not recorded.strip():
        return ()
    return (DiffSourceDocument(scope, recorded, False),)


def _normalize_payload(
    payload: DiffSourcePayload, requested: DiffScope
) -> tuple[DiffSourceDocument, ...]:
    if isinstance(payload, str):
        return (DiffSourceDocument(DiffScope.UNSTAGED, payload, True),)
    if isinstance(payload, DiffSourceDocument):
        return (payload,)
    if not isinstance(payload, Sequence):
        raise TypeError("diff source returned an unsupported payload")
    documents = tuple(payload)
    if any(not isinstance(document, DiffSourceDocument) for document in documents):
        raise TypeError("diff source sequence must contain only DiffSourceDocument values")
    return documents


def _select_live_documents(
    documents: tuple[DiffSourceDocument, ...], requested: DiffScope
) -> tuple[DiffSourceDocument, ...]:
    if requested is not DiffScope.WORKING_TREE:
        return tuple(document for document in documents if document.scope is requested)
    facets = tuple(
        document for document in documents if document.scope in _WORKING_TREE_FACETS
    )
    if facets:
        return facets
    return tuple(document for document in documents if document.scope is requested)


def _normalized(unified: str) -> str:
    return unified.replace("\r\n", "\n").replace("\r", "\n")


def _diff_header(current: FileDiff, stale: bool, selected: int, total: int) -> str:
    provenance = "fresh" if current.fresh else "recorded"
    stale_label = " · stale" if stale else ""
    return (
        f"{current.path} · {current.scope.value} · {provenance}{stale_label} · "
        f"+{current.additions} -{current.removals} · file {selected}/{total}"
    )
