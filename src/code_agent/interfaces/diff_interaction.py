from __future__ import annotations

from enum import Enum

from ._diff_parser import DiffLine, DiffLineKind, DiffScope
from .diff_feedback import format_diff_feedback
from .diff_interaction_render import DIFF_PAGE_SIZE, render_diff_interaction
from .diff_view import DiffView, MAX_DIFF_COMMENT_CHARS
from .input_buffer import InputBuffer
from .input_events import paste_event


class DiffMode(str, Enum):
    BROWSE = "browse"
    FILTER = "filter"
    COMMENT = "comment"
    DISCARD = "discard"


class DiffAction(str, Enum):
    NONE = "none"
    REFRESH = "refresh"
    SEND = "send"


class DiffInteraction:
    """Own a point-in-time diff snapshot and consume its modal keyboard input."""

    def __init__(self) -> None:
        self._active = False
        self._view: DiffView | None = None
        self.mode = DiffMode.BROWSE
        self.line_index = 0
        self.editor = InputBuffer()
        self.error: str | None = None
        self.scope = DiffScope.WORKING_TREE
        self.recorded_diff: str | None = None
        self.paths: tuple[str, ...] = ()
        self._before_discard = DiffMode.BROWSE

    @property
    def active(self) -> bool:
        return self._active

    @property
    def view(self) -> DiffView | None:
        return self._view

    @property
    def has_unsent_comments(self) -> bool:
        return bool(self._view and self._view.comments)

    def open(
        self,
        view: DiffView | None,
        *,
        scope: DiffScope,
        recorded_diff: str | None,
        paths: tuple[str, ...] = (),
        error: str | None = None,
    ) -> None:
        self._active = True
        self._view = view
        self.scope, self.recorded_diff, self.paths = scope, recorded_diff, paths
        self.mode = DiffMode.BROWSE
        self.line_index = 0
        self._before_discard = DiffMode.BROWSE
        self.editor.clear()
        self.error = _bounded_error(error)

    def replace_view(self, view: DiffView) -> None:
        if self.has_unsent_comments:
            raise RuntimeError("send or discard comments before refreshing")
        old = self._view.current if self._view else None
        query = self._view.path_filter if self._view else ""
        view.filter(query)
        if old is not None:
            for index, item in enumerate(view.files):
                if (item.scope, item.path) == (old.scope, old.path):
                    view.selected_file = index
                    break
        self._view = view
        self.line_index = 0
        self.error = None

    def set_error(self, message: str) -> None:
        self.error = _bounded_error(message)

    def request_close(self) -> None:
        if self.mode is DiffMode.DISCARD:
            self.mode = self._before_discard
            return
        has_draft = self.mode is DiffMode.COMMENT and bool(self.editor.text)
        if self.has_unsent_comments or has_draft:
            self._before_discard = self.mode
            self.mode = DiffMode.DISCARD
        else:
            self._close()

    def finish_send(self) -> None:
        """Close only after the normal submit boundary accepted the feedback."""
        self._close()

    def rows(self, columns: int, *, max_rows: int = 14) -> tuple[str, ...]:
        return render_diff_interaction(
            self._view,
            mode=self.mode.value,
            line_index=self.line_index,
            editor_text=self.editor.text,
            editor_cursor=self.editor.cursor,
            error=self.error,
            columns=columns,
            max_rows=max_rows,
        )

    def handle_key(self, key: str) -> DiffAction:
        if not self._active:
            return DiffAction.NONE
        if self.mode is DiffMode.DISCARD:
            self._handle_discard_key(key)
            return DiffAction.NONE
        if self.mode in {DiffMode.FILTER, DiffMode.COMMENT}:
            self._handle_editor_key(key)
            return DiffAction.NONE
        return self._handle_browse_key(key)

    def feedback(self) -> str:
        comments = self._view.comments if self._view else ()
        return format_diff_feedback(comments)

    def _handle_browse_key(self, key: str) -> DiffAction:
        if key in {"\x1b", "q"}:
            self.request_close()
        elif key in {"up", "k"}:
            self._move_line(-1)
        elif key in {"down", "j"}:
            self._move_line(1)
        elif key in {"left", "p"}:
            self._move_file(-1)
        elif key in {"right", "n"}:
            self._move_file(1)
        elif key == "[":
            self._move_hunk(-1)
        elif key == "]":
            self._move_hunk(1)
        elif key == "page_up":
            self._move_line(-DIFF_PAGE_SIZE)
        elif key == "page_down":
            self._move_line(DIFF_PAGE_SIZE)
        elif key == "/":
            self._begin_edit(DiffMode.FILTER)
        elif key == "c":
            self._begin_edit(DiffMode.COMMENT)
        elif key == "r":
            return DiffAction.REFRESH
        elif key == "s":
            return DiffAction.SEND
        return DiffAction.NONE

    def _handle_editor_key(self, key: str) -> None:
        if key == "\x1b":
            self.mode = DiffMode.BROWSE
            self.editor.clear()
        elif key == "\r":
            self._commit_editor()
        elif key == "\n":
            if self.mode is DiffMode.COMMENT:
                self._insert_editor("\n")
        elif key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
            try:
                value = paste_event(key[6:-6]).value
            except (TypeError, ValueError) as error:
                self.set_error(str(error))
            else:
                self._insert_editor(value)
        elif key == "\x15":
            self.editor.clear()
        elif key in {"\x08", "\x7f"}:
            self.editor.backspace()
        elif key == "delete":
            self.editor.delete()
        elif key in {"left", "right", "home", "end", "up", "down"}:
            _move_editor(self.editor, key)
        elif key.isprintable():
            self._insert_editor(key)

    def _handle_discard_key(self, key: str) -> None:
        if key.casefold() == "y":
            self._close()
        elif key in {"\r", "\n", "\x1b"} or key.casefold() == "n":
            self.mode = self._before_discard

    def _begin_edit(self, mode: DiffMode) -> None:
        if mode is DiffMode.COMMENT and not self._current_lines():
            self.set_error("select a diff line before commenting")
            return
        self.mode = mode
        value = self._view.path_filter if mode is DiffMode.FILTER and self._view else ""
        self.editor.replace(value)
        self.error = None

    def _commit_editor(self) -> None:
        if self.mode is DiffMode.FILTER and self._view:
            self._view.filter(self.editor.text.replace("\n", " "))
            self.line_index = 0
        elif self.mode is DiffMode.COMMENT and self._view:
            comment = None
            try:
                comment = self._view.comment(self.line_index, self.editor.text)
                self.feedback()
            except (TypeError, ValueError) as error:
                if comment is not None:
                    self._view.remove_comment(comment)
                self.set_error(str(error))
                return
        self.mode = DiffMode.BROWSE
        self.editor.clear()

    def _insert_editor(self, value: str) -> None:
        limit = 512 if self.mode is DiffMode.FILTER else MAX_DIFF_COMMENT_CHARS
        if len(self.editor.text) + len(value) > limit:
            self.set_error(f"{self.mode.value} text is too large")
            return
        self.editor.insert(value)

    def _move_line(self, offset: int) -> None:
        lines = self._current_lines()
        if lines:
            self.line_index = min(max(0, self.line_index + offset), len(lines) - 1)

    def _move_file(self, offset: int) -> None:
        if self._view:
            self._view.move_file(offset)
            self.line_index = 0

    def _move_hunk(self, offset: int) -> None:
        hunks = [
            index
            for index, line in enumerate(self._current_lines())
            if line.kind is DiffLineKind.HUNK
        ]
        if not hunks:
            return
        candidates = [item for item in hunks if item > self.line_index]
        if offset < 0:
            candidates = [item for item in hunks if item < self.line_index]
            self.line_index = candidates[-1] if candidates else hunks[-1]
        else:
            self.line_index = candidates[0] if candidates else hunks[0]

    def _current_lines(self) -> tuple[DiffLine, ...]:
        current = self._view.current if self._view else None
        return current.lines if current else ()

    def _close(self) -> None:
        self._active = False
        self._view = None
        self.mode = DiffMode.BROWSE
        self._before_discard = DiffMode.BROWSE
        self.editor.clear()
        self.error = None


def _move_editor(editor: InputBuffer, key: str) -> None:
    actions = {
        "left": editor.move_left,
        "right": editor.move_right,
        "home": editor.move_home,
        "end": editor.move_end,
        "up": editor.move_up,
        "down": editor.move_down,
    }
    actions[key]()


def _bounded_error(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\n", " ")[:256]
