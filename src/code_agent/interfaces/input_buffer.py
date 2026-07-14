from __future__ import annotations


class InputBuffer:
    """A small multiline editor for raw Windows console key events."""

    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft = ""

    def insert(self, value: str) -> None:
        self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
        self.cursor += len(value)

    def insert_line_break(self) -> None:
        self.insert("\n")

    def move_left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_right(self) -> None:
        self.cursor = min(len(self.text), self.cursor + 1)

    def move_home(self) -> None:
        self.cursor = self.text.rfind("\n", 0, self.cursor) + 1

    def move_end(self) -> None:
        line_end = self.text.find("\n", self.cursor)
        self.cursor = len(self.text) if line_end < 0 else line_end

    def move_up(self) -> bool:
        line_start = self.text.rfind("\n", 0, self.cursor) + 1
        if line_start == 0:
            return False
        column = self.cursor - line_start
        previous_end = line_start - 1
        previous_start = self.text.rfind("\n", 0, previous_end) + 1
        self.cursor = previous_start + min(column, previous_end - previous_start)
        return True

    def move_down(self) -> bool:
        line_start = self.text.rfind("\n", 0, self.cursor) + 1
        line_end = self.text.find("\n", self.cursor)
        if line_end < 0:
            return False
        column = self.cursor - line_start
        next_start = line_end + 1
        next_end = self.text.find("\n", next_start)
        if next_end < 0:
            next_end = len(self.text)
        self.cursor = next_start + min(column, next_end - next_start)
        return True

    def backspace(self) -> None:
        if self.cursor:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    def delete(self) -> None:
        self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0
        self._history_index = None

    def previous(self) -> None:
        if not self._history: return
        if self._history_index is None: self._draft = self.text; self._history_index = len(self._history) - 1
        else: self._history_index = max(0, self._history_index - 1)
        self._set(self._history[self._history_index])

    def next(self) -> None:
        if self._history_index is None: return
        if self._history_index >= len(self._history) - 1: self._history_index = None; self._set(self._draft)
        else: self._history_index += 1; self._set(self._history[self._history_index])

    def submit(self) -> str:
        value = self.text
        if value.strip() and (not self._history or self._history[-1] != value): self._history.append(value)
        self.clear()
        return value

    def _set(self, value: str) -> None: self.text = value; self.cursor = len(value)
